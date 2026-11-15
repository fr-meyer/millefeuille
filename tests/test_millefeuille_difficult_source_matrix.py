"""Offline route dependency contracts, not PDF extraction or provider acceptance."""

from pathlib import Path
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError, RouteSelection
from millefeuille.domain.route_fixtures import _validate_route_dependencies


class DifficultSourceRouteMatrixTests(unittest.TestCase):
    def test_each_route_requires_its_extraction_evidence(self):
        for route, native, ocr in (
            (RouteSelection.NATIVE, None, None),
            (RouteSelection.NATIVE, None, {"provider": "mistral-ocr"}),
            (RouteSelection.OCR, None, None),
            (RouteSelection.OCR, {}, None),
            (RouteSelection.MERGED_DUAL, None, None),
            (RouteSelection.MERGED_DUAL, {}, None),
            (RouteSelection.MERGED_DUAL, None, {"provider": "mistral-ocr"}),
        ):
            with (
                self.subTest(route=route, native=native, ocr=ocr),
                self.assertRaises(MillefeuilleContractError),
            ):
                _validate_route_dependencies(
                    selected_route=route,
                    native_payload=native,
                    ocr_payload=ocr,
                    source_pack_dir=Path("synthetic-pack"),
                )

    def test_native_ocr_and_dual_report_only_present_evidence(self):
        for route, native, ocr, expected in (
            (RouteSelection.NATIVE, {}, None, ["native"]),
            (RouteSelection.OCR, None, {"provider": "mistral-ocr"}, ["mistral-ocr"]),
            (
                RouteSelection.MERGED_DUAL,
                {},
                {"provider": "mistral-ocr"},
                ["native", "mistral-ocr"],
            ),
        ):
            with self.subTest(route=route):
                self.assertEqual(
                    _validate_route_dependencies(
                        selected_route=route,
                        native_payload=native,
                        ocr_payload=ocr,
                        source_pack_dir=Path("synthetic-pack"),
                    ),
                    expected,
                )
