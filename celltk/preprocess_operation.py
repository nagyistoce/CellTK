from __future__ import division
from utils.filters import calc_lapgauss
import SimpleITK as sitk
import numpy as np
from utils.preprocess_utils import homogenize_intensity_n4
from utils.preprocess_utils import convert_positive, estimate_background_prc
from utils.preprocess_utils import resize_img
from utils.preprocess_utils import histogram_matching, wavelet_subtraction_hazen
from utils.filters import adaptive_thresh
from utils.global_holder import holder
from utils.cp_functions import align_cross_correlation, align_mutual_information
from scipy.ndimage import imread
from glob import glob
from utils.filters import interpolate_nan


def gaussian_laplace(img, SIGMA=2.5, NEG=False):
    if NEG:
        img = -calc_lapgauss(img, SIGMA)
        img[img < 0 ] = 0
        return img
    return calc_lapgauss(img, SIGMA)


def curvature_anisotropic_smooth(img, NITER=10):
    fil = sitk.CurvatureAnisotropicDiffusionImageFilter()
    fil.SetNumberOfIterations(NITER)
    simg = sitk.GetImageFromArray(img.astype(np.float32))
    sres = fil.Execute(simg)
    return sitk.GetArrayFromImage(sres)


def background_subtraction_wavelet_hazen(img, THRES=100, ITER=5, WLEVEL=6, OFFSET=50):
    """Wavelet background subtraction.
    """
    back = wavelet_subtraction_hazen(img, ITER=ITER, THRES=THRES, WLEVEL=WLEVEL)
    img = img - back
    return convert_positive(img, OFFSET)


def n4_illum_correction(img, RATIO=1.5, FILTERINGSIZE=50):
    """
    Implementation of the N4 bias field correction algorithm.
    Takes some calculation time. It first calculates the background using adaptive_thesh.
    """
    bw = adaptive_thresh(img, R=RATIO, FILTERINGSIZE=FILTERINGSIZE)
    img = homogenize_intensity_n4(img, -bw)
    return img


def n4_illum_correction_downsample(img, DOWN=2, RATIO=1.05, FILTERINGSIZE=50, OFFSET=10):
    """Faster but more insensitive to local illum bias.
    """
    fil = sitk.ShrinkImageFilter()
    cc = sitk.GetArrayFromImage(fil.Execute(sitk.GetImageFromArray(img), [DOWN, DOWN]))
    bw = adaptive_thresh(cc, R=RATIO, FILTERINGSIZE=FILTERINGSIZE/DOWN)
    himg = homogenize_intensity_n4(cc, -bw)
    himg = cc - himg
    # himg[himg < 0] = 0
    bias = resize_img(himg, img.shape)
    img = img - bias
    return convert_positive(img, OFFSET)


def align(img, CROP=0.05):
    """
    CROP (float): crop images beforehand. When set to 0.05, 5% of each edges are cropped.
    """
    if not hasattr(holder, "align"):
        if isinstance(holder.inputs[0], list) or isinstance(holder.inputs[0], tuple):
            inputs = [i[0] for i in holder.inputs]
        else:
            inputs = holder.inputs

        img0 = imread(inputs[0])
        shapes = img0.shape

        (ch, cw) = [int(CROP * i) for i in img0.shape]
        ch = None if ch == 0 else ch
        cw = None if cw == 0 else cw

        img0 = img0[ch:-ch, cw:-cw]
        mask = np.ones(img0.shape, np.bool)
        store = [(0, 0)]
        for path0, path1 in zip(inputs[:-1], inputs[1:]):
            img0 = imread(path0)[ch:-ch, cw:-cw]
            img1 = imread(path1)[ch:-ch, cw:-cw]

            j0, j1, score0 = align_mutual_information(img0, img1, mask, mask)

            """compare it to FFT based alignment and pick the higher mutual info.
            align_mutual_information can often stack in a local solution.
            """
            from utils.mi_align import mutualinf, offset_slice
            from utils.imreg import translation
            jj1, jj0 = translation(img0, img1)
            p2, p1 = offset_slice(img1, img0, jj1, jj0)
            m2, m1 = offset_slice(mask, mask, jj1, jj0)
            score1 = mutualinf(p1, p2, m1, m2)
            if score1 > score0:
                j0, j1 = jj0, jj1
            store.append((store[-1][0] + j0, store[-1][1] + j1))

        max_w = max(i[0] for i in store)
        start_w = [max_w - i[0] for i in store]
        size_w = min([shapes[1] + i[0] for i in store]) - max_w
        max_h = max(i[1] for i in store)
        start_h = [max_h - i[1] for i in store]
        size_h = min([shapes[0] + i[1] for i in store]) - max_h
        holder.align = [(hi, hi+size_h, wi, wi+size_w) for hi, wi in zip(start_h, start_w)]

    jt = holder.align[holder.frame]
    if img.ndim == 2:
        return img[jt[0]:jt[1], jt[2]:jt[3]]
    if img.ndim == 3:
        return img[jt[0]:jt[1], jt[2]:jt[3], :]




def flatfield_references(img, ff_paths=['Pos0/img00.tif', 'Pos1/img01.tif']):
    """
    Use empty images for background subtraction and illumination bias correction.
    Given multiple reference images, it will calculate median profile and use it for subtraction.

    ff_paths (str or List(str)): image path for flat fielding references.
                                 It can be single, multiple or path with wildcards.

        e.g.    ff_paths = "FF/img_000000000_YFP*"
                ff_paths = ["FF/img_01.tif", "FF/img_02.tif"]
    """
    store = []
    if isinstance(ff_paths, str):
        ff_paths = [ff_paths, ]
    for i in ff_paths:
        for ii in glob(i):
            store.append(ii)
    ff_store = []
    for path in store:
        ff_store.append(imread(path))
    ff = np.median(np.dstack(ff_store), axis=2)
    img = img - ff
    img[img < 0] = np.nan
    img = interpolate_nan(img)
    return img


def histogram_match(img, BINS=1000, QUANT=100, THRES=False):
    """
    If an optical system is not stable and shows global intensity changes over time,
    use this method to correct for it. Typically use for nuclear marker, where
    intensity and its patterns should be stable over time.
    """
    if holder.frame == 0:
        holder.first_img = img
    else:
        img = histogram_matching(img, holder.first_img, BINS, QUANT, THRES)
    return img
