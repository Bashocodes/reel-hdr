"""Standards-defined transfer functions used by Reel-HDR.

The functions in this module operate on normalized floating-point code values.
They are deliberately limited to transfer math: no content-dependent tone curve,
gamut transform, or creative grading is applied.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

type FloatResult = float | NDArray[np.float64]

# SMPTE ST 2084 constants, also specified by ITU-R BT.2100 for the PQ system.
_PQ_M1 = 2610.0 / 16384.0
_PQ_M2 = 2523.0 / 32.0
_PQ_C1 = 3424.0 / 4096.0
_PQ_C2 = 2413.0 / 128.0
_PQ_C3 = 2392.0 / 128.0
_PQ_PEAK_NITS = 10_000.0

# ITU-R BT.2100 reference HLG OETF constants.
_HLG_A = 0.17883277
_HLG_B = 1.0 - 4.0 * _HLG_A
_HLG_C = 0.5 - _HLG_A * np.log(4.0 * _HLG_A)
_HLG_BREAK_SCENE = 1.0 / 12.0
_HLG_BREAK_SIGNAL = 0.5


def _result(value: NDArray[np.float64], *, scalar: bool) -> FloatResult:
    """Return a Python float for scalar input and an array otherwise."""

    return float(value) if scalar else value


def _normalized(values: ArrayLike) -> tuple[NDArray[np.float64], bool]:
    array = np.asarray(values, dtype=np.float64)
    return np.clip(array, 0.0, 1.0), array.ndim == 0


def pq_eotf(code_value: ArrayLike) -> FloatResult:
    """Decode a normalized PQ code value to absolute luminance in cd/m² (nits).

    This implements the SMPTE ST 2084 EOTF in ITU-R BT.2100-3 (02/2025),
    Table 4, “Perceptual Quantization (PQ) system reference non-linear
    transfer functions”:

    ``L = 10000 * (max(N**(1/m2) - c1, 0) /
    (c2 - c3*N**(1/m2)))**(1/m1)``

    where ``m1=2610/16384``, ``m2=2523/32``, ``c1=3424/4096``,
    ``c2=2413/128``, and ``c3=2392/128``.

    Input is clipped to PQ's normalized ``[0, 1]`` domain. NaNs propagate;
    infinities saturate through clipping. A scalar input returns ``float`` and
    any array-like input returns a ``float64`` NumPy array of the same shape.
    """

    code, scalar = _normalized(code_value)
    power = np.power(code, 1.0 / _PQ_M2)
    numerator = np.maximum(power - _PQ_C1, 0.0)
    denominator = _PQ_C2 - _PQ_C3 * power
    luminance = _PQ_PEAK_NITS * np.power(numerator / denominator, 1.0 / _PQ_M1)
    return _result(luminance, scalar=scalar)


def pq_inverse_eotf(luminance_nits: ArrayLike) -> FloatResult:
    """Encode absolute luminance in cd/m² as a normalized PQ code value.

    This is the inverse of the ST 2084/ITU-R BT.2100-3 Table 4 PQ EOTF:

    ``N = ((c1 + c2*Y**m1) / (1 + c3*Y**m1))**m2``

    where ``Y = L/10000`` and the constants are the exact rational values
    listed in :func:`pq_eotf`.

    Luminance is clipped to PQ's absolute ``[0, 10000]`` cd/m² range. NaNs
    propagate; infinities saturate through clipping. Scalar/array return
    behavior matches :func:`pq_eotf`.
    """

    luminance = np.asarray(luminance_nits, dtype=np.float64)
    scalar = luminance.ndim == 0
    relative = np.clip(luminance, 0.0, _PQ_PEAK_NITS) / _PQ_PEAK_NITS
    power = np.power(relative, _PQ_M1)
    code = np.power(
        (_PQ_C1 + _PQ_C2 * power) / (1.0 + _PQ_C3 * power),
        _PQ_M2,
    )
    return _result(code, scalar=scalar)


def hlg_oetf(scene_linear: ArrayLike) -> FloatResult:
    """Apply the ITU-R BT.2100 reference HLG OETF.

    For relative scene light ``E``, ITU-R BT.2100-3 (02/2025), Table 5,
    “Hybrid Log-Gamma (HLG) system reference non-linear transfer functions,”
    gives the exact piecewise definition:

    ``E' = sqrt(3*E)`` for ``0 <= E <= 1/12``;
    ``E' = a*ln(12*E - b) + c`` otherwise,

    with ``a=0.17883277``, ``b=1-4a``, and ``c=0.5-a*ln(4a)``.

    Input is clipped to the reference ``[0, 1]`` scene-light domain. The two
    branches are evaluated through masks, so the logarithm is never evaluated
    for an out-of-domain low-branch sample. NaNs propagate. Scalar/array return
    behavior matches :func:`pq_eotf`.
    """

    scene, scalar = _normalized(scene_linear)
    signal = np.empty_like(scene)
    low = scene <= _HLG_BREAK_SCENE
    high = ~low

    signal[low] = np.sqrt(3.0 * scene[low])
    signal[high] = _HLG_A * np.log(12.0 * scene[high] - _HLG_B) + _HLG_C
    return _result(signal, scalar=scalar)


def hlg_inverse_oetf(signal_value: ArrayLike) -> FloatResult:
    """Invert the ITU-R BT.2100 reference HLG OETF to relative scene light.

    ITU-R BT.2100-3 (02/2025), Table 5 gives the inverse piecewise definition:

    ``E = E'**2 / 3`` for ``0 <= E' <= 1/2``;
    ``E = (exp((E' - c)/a) + b) / 12`` otherwise,

    using the constants documented by :func:`hlg_oetf`.

    Input is clipped to the normalized ``[0, 1]`` signal domain. Branches are
    evaluated with masks. NaNs propagate, and scalar/array return behavior
    matches :func:`pq_eotf`.
    """

    signal, scalar = _normalized(signal_value)
    scene = np.empty_like(signal)
    low = signal <= _HLG_BREAK_SIGNAL
    high = ~low

    scene[low] = np.square(signal[low]) / 3.0
    scene[high] = (np.exp((signal[high] - _HLG_C) / _HLG_A) + _HLG_B) / 12.0
    return _result(scene, scalar=scalar)


def pq_to_hlg_scene_light(
    pq_code: ArrayLike,
    nominal_peak_nits: float = 1000.0,
) -> FloatResult:
    """Convert PQ samples to HLG through a neutral scene-light representation.

    The ordered conversion is explicit: decode ST 2084 with :func:`pq_eotf`,
    normalize absolute luminance by ``nominal_peak_nits`` to obtain relative
    scene light, then apply the BT.2100 HLG OETF with :func:`hlg_oetf`.

    ``nominal_peak_nits`` is the sole mapping policy and must be finite and
    positive. Samples above that ceiling are clipped rather than passed through
    a creative roll-off. The default is the conventional 1000 cd/m² nominal HLG
    reference peak. PQ input follows :func:`pq_eotf`'s clipping policy.
    """

    if not np.isfinite(nominal_peak_nits) or nominal_peak_nits <= 0.0:
        raise ValueError("nominal_peak_nits must be finite and greater than zero")

    luminance = pq_eotf(pq_code)
    relative_scene = np.asarray(luminance, dtype=np.float64) / nominal_peak_nits
    converted = hlg_oetf(relative_scene)

    if np.asarray(pq_code).ndim == 0:
        return float(converted)
    return np.asarray(converted, dtype=np.float64)


__all__ = [
    "hlg_inverse_oetf",
    "hlg_oetf",
    "pq_eotf",
    "pq_inverse_eotf",
    "pq_to_hlg_scene_light",
]
