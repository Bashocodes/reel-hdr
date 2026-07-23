from __future__ import annotations

import numpy as np
import pytest

from reelhdr.transfer import (
    hlg_inverse_oetf,
    hlg_oetf,
    pq_eotf,
    pq_inverse_eotf,
    pq_to_hlg_scene_light,
)


def test_pq_known_100_nit_anchor() -> None:
    assert pq_eotf(0.5080784215) == pytest.approx(100.0, rel=1e-8)
    assert pq_eotf(0.508) == pytest.approx(100.0, rel=2e-3)
    assert pq_inverse_eotf(100.0) == pytest.approx(0.5080784215, rel=1e-8)


def test_pq_endpoints_and_clipping() -> None:
    values = pq_eotf(np.array([-1.0, 0.0, 1.0, 2.0]))
    np.testing.assert_allclose(values, [0.0, 0.0, 10_000.0, 10_000.0])

    # ST 2084's inverse formula maps zero luminance to c1**m2, a tiny
    # positive code that the EOTF maps back to exact black.
    black_code = pq_inverse_eotf(0.0)
    codes = pq_inverse_eotf(np.array([-10.0, 0.0, 10_000.0, 20_000.0]))
    np.testing.assert_allclose(
        codes,
        [black_code, black_code, 1.0, 1.0],
        atol=1e-12,
    )


def test_pq_round_trip_is_tight() -> None:
    codes = np.linspace(0.0, 1.0, 257)
    decoded = pq_eotf(codes)
    np.testing.assert_allclose(pq_inverse_eotf(decoded), codes, atol=8e-7, rtol=0.0)

    luminance = np.geomspace(1e-4, 10_000.0, 257)
    encoded = pq_inverse_eotf(luminance)
    np.testing.assert_allclose(pq_eotf(encoded), luminance, rtol=1e-11, atol=1e-10)


def test_hlg_known_anchors_and_clipping() -> None:
    encoded = hlg_oetf(np.array([-1.0, 0.0, 1.0 / 12.0, 1.0, 2.0]))
    np.testing.assert_allclose(encoded, [0.0, 0.0, 0.5, 1.0, 1.0], atol=1e-8)

    decoded = hlg_inverse_oetf(np.array([-1.0, 0.0, 0.5, 1.0, 2.0]))
    np.testing.assert_allclose(decoded, [0.0, 0.0, 1.0 / 12.0, 1.0, 1.0], atol=3e-8)


def test_hlg_round_trip_is_tight_across_both_branches() -> None:
    scene = np.concatenate(
        (
            np.linspace(0.0, 1.0 / 12.0, 100),
            np.linspace(1.0 / 12.0, 1.0, 200),
        )
    )
    np.testing.assert_allclose(
        hlg_inverse_oetf(hlg_oetf(scene)),
        scene,
        atol=2e-12,
        rtol=0.0,
    )


def test_hlg_masks_prevent_invalid_inactive_branch_evaluation() -> None:
    with np.errstate(all="raise"):
        encoded = hlg_oetf(np.array([0.0, 1.0 / 24.0, 1.0 / 12.0, 0.5, 1.0]))
        decoded = hlg_inverse_oetf(encoded)

    assert np.all(np.isfinite(encoded))
    assert np.all(np.isfinite(decoded))


def test_pq_to_hlg_matches_explicit_stages() -> None:
    pq_codes = np.array([0.0, 0.5080784215, 0.75, 1.0])
    expected = hlg_oetf(np.asarray(pq_eotf(pq_codes)) / 1000.0)

    np.testing.assert_allclose(
        pq_to_hlg_scene_light(pq_codes),
        expected,
        atol=1e-12,
        rtol=0.0,
    )
    assert pq_to_hlg_scene_light(0.5080784215) == pytest.approx(
        hlg_oetf(0.1),
        rel=1e-8,
    )
    assert pq_to_hlg_scene_light(1.0) == pytest.approx(1.0)


@pytest.mark.parametrize("peak", [0.0, -1.0, float("inf"), float("nan")])
def test_pq_to_hlg_rejects_invalid_nominal_peak(peak: float) -> None:
    with pytest.raises(ValueError, match="nominal_peak_nits"):
        pq_to_hlg_scene_light(0.5, nominal_peak_nits=peak)


def test_scalar_array_and_nan_behavior() -> None:
    assert isinstance(pq_eotf(0.5), float)
    assert isinstance(pq_inverse_eotf(100.0), float)
    assert isinstance(hlg_oetf(0.5), float)
    assert isinstance(hlg_inverse_oetf(0.5), float)
    assert isinstance(pq_to_hlg_scene_light(0.5), float)

    shape = (2, 3)
    array_result = pq_to_hlg_scene_light(np.full(shape, 0.5))
    assert isinstance(array_result, np.ndarray)
    assert array_result.shape == shape
    assert array_result.dtype == np.float64

    assert np.isnan(pq_eotf(float("nan")))
    assert np.isnan(pq_inverse_eotf(float("nan")))
    assert np.isnan(hlg_oetf(float("nan")))
    assert np.isnan(hlg_inverse_oetf(float("nan")))
