"""Unit tests for ZoteroClient tag-query pagination and selection logic."""

import logging
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from millefeuille.clients.exceptions import ZoteroAPIError, ZoteroAuthError
from millefeuille.clients.zotero_client import ZoteroClient
from millefeuille.domain.config import TagRuleConfig, TagSelectionConfig


def _make_client():
    """Build a ZoteroClient without running __init__ (no real credentials)."""
    client = object.__new__(ZoteroClient)
    client._zotero_read = MagicMock()
    stub_config = MagicMock()
    stub_config.library_id = "0"
    client.config = stub_config
    client.credentials = stub_config
    return client, client._zotero_read


def _make_raw_item(key, extra_data=None):
    """Return a well-formed raw Zotero API item dict."""
    data = {"key": key, "tags": []}
    if extra_data:
        data.update(extra_data)
    return {"key": key, "data": data}


class TestFetchItemsForTagContract(unittest.TestCase):
    """Core contract for _fetch_items_for_tag."""

    def test_calls_everything_wrapping_items(self):
        client, mock_zr = _make_client()
        items_iter = object()
        mock_zr.items.return_value = items_iter

        client._fetch_items_for_tag("millefeuille")

        mock_zr.items.assert_called_once_with(tag="millefeuille")
        mock_zr.everything.assert_called_once_with(items_iter)

    def test_complete_result_set_mapped(self):
        client, mock_zr = _make_client()
        raw = [_make_raw_item(f"K{i}", {"title": f"T{i}"}) for i in range(1, 4)]
        mock_zr.everything.return_value = raw

        result = client._fetch_items_for_tag("millefeuille")

        self.assertEqual(len(result), 3)
        for i in range(1, 4):
            key = f"K{i}"
            self.assertEqual(result[key], raw[i - 1]["data"])

    def test_duplicate_keys_collapse(self):
        client, mock_zr = _make_client()
        mock_zr.everything.return_value = [
            _make_raw_item("K1", {"title": "first"}),
            _make_raw_item("K1", {"title": "second"}),
        ]

        result = client._fetch_items_for_tag("millefeuille")

        self.assertEqual(len(result), 1)
        self.assertIn("K1", result)

    def test_malformed_non_dict_skipped(self):
        client, mock_zr = _make_client()
        mock_zr.everything.return_value = [
            None,
            "string",
            42,
            _make_raw_item("K1"),
        ]

        result = client._fetch_items_for_tag("millefeuille")

        self.assertEqual(list(result.keys()), ["K1"])

    def test_malformed_missing_data_skipped(self):
        client, mock_zr = _make_client()
        mock_zr.everything.return_value = [
            {"key": "K1"},
            _make_raw_item("K2"),
        ]

        result = client._fetch_items_for_tag("millefeuille")

        self.assertEqual(list(result.keys()), ["K2"])

    def test_malformed_missing_key_skipped(self):
        client, mock_zr = _make_client()
        mock_zr.everything.return_value = [
            {"data": {"title": "x"}},
            _make_raw_item("K1"),
        ]

        result = client._fetch_items_for_tag("millefeuille")

        self.assertEqual(list(result.keys()), ["K1"])


class TestFetchItemsForTagLogging(unittest.TestCase):
    """Pagination-related log behaviour for _fetch_items_for_tag."""

    def test_single_page_no_info_log(self):
        client, mock_zr = _make_client()
        mock_zr.everything.return_value = [_make_raw_item(f"K{i}") for i in range(5)]

        with self.assertLogs(
            "millefeuille.clients.zotero_client", level="DEBUG"
        ) as logs:
            client._fetch_items_for_tag("millefeuille")

        def is_fetch_completion(record):
            msg = record.getMessage()
            return "Fetched" in msg and "items for tag" in msg

        info_completion = [
            r
            for r in logs.records
            if r.levelno == logging.INFO and is_fetch_completion(r)
        ]
        self.assertEqual(info_completion, [])

        debug_completion = [
            r
            for r in logs.records
            if r.levelno == logging.DEBUG and is_fetch_completion(r)
        ]
        self.assertEqual(len(debug_completion), 1)
        self.assertEqual(
            debug_completion[0].getMessage(),
            "Fetched 5 items for tag 'millefeuille'",
        )

    def test_large_result_emits_info_log(self):
        client, mock_zr = _make_client()
        mock_zr.everything.return_value = [_make_raw_item(f"K{i}") for i in range(150)]

        with self.assertLogs(
            "millefeuille.clients.zotero_client", level="DEBUG"
        ) as logs:
            client._fetch_items_for_tag("mytag")

        info_records = [r for r in logs.records if r.levelno == logging.INFO]
        matching = [
            r
            for r in info_records
            if "150" in r.getMessage()
            and "mytag" in r.getMessage()
            and "paginated" in r.getMessage()
        ]
        self.assertGreaterEqual(len(matching), 1)


class TestFetchItemsForTagErrors(unittest.TestCase):
    """Error mapping in _fetch_items_for_tag."""

    def test_http_401_raises_zotero_auth_error(self):
        client, mock_zr = _make_client()
        mock_zr.everything.side_effect = HTTPError(
            url=None, code=401, msg="Unauthorized", hdrs=None, fp=None
        )
        with self.assertRaises(ZoteroAuthError):
            client._fetch_items_for_tag("millefeuille")

    def test_http_403_raises_zotero_auth_error(self):
        client, mock_zr = _make_client()
        mock_zr.everything.side_effect = HTTPError(
            url=None, code=403, msg="Forbidden", hdrs=None, fp=None
        )
        with self.assertRaises(ZoteroAuthError):
            client._fetch_items_for_tag("millefeuille")

    def test_http_500_raises_zotero_api_error(self):
        client, mock_zr = _make_client()
        mock_zr.everything.side_effect = HTTPError(
            url=None, code=500, msg="Server Error", hdrs=None, fp=None
        )
        with self.assertRaises(ZoteroAPIError):
            client._fetch_items_for_tag("millefeuille")

    def test_url_error_raises_zotero_api_error(self):
        client, mock_zr = _make_client()
        mock_zr.everything.side_effect = URLError(reason="timeout")
        with self.assertRaises(ZoteroAPIError):
            client._fetch_items_for_tag("millefeuille")

    def test_unexpected_error_raises_zotero_api_error(self):
        client, mock_zr = _make_client()
        mock_zr.everything.side_effect = RuntimeError("boom")
        with self.assertRaises(ZoteroAPIError):
            client._fetch_items_for_tag("millefeuille")


class TestDownloadPdfLogging(unittest.TestCase):
    """PDF download logging must not expose transient signed storage URLs."""

    def test_httpx_info_logging_suppressed_and_restored(self):
        client, mock_zr = _make_client()
        observed_httpx_levels = []

        def file_side_effect(_attachment_key):
            observed_httpx_levels.append(logging.getLogger("httpx").level)
            return b"%PDF-1.7"

        mock_zr.file.side_effect = file_side_effect
        httpx_logger = logging.getLogger("httpx")
        previous_level = httpx_logger.level
        try:
            httpx_logger.setLevel(logging.INFO)

            pdf_bytes = client.download_pdf("ITEM1", "ATT1")

            self.assertEqual(pdf_bytes, b"%PDF-1.7")
            self.assertEqual(observed_httpx_levels, [logging.WARNING])
            self.assertEqual(httpx_logger.level, logging.INFO)
        finally:
            httpx_logger.setLevel(previous_level)


def _item_data(key, tags=None):
    data = {"key": key, "title": f"Title {key}", "tags": tags or []}
    return data


class TestGetItemsBySelectionSetLogic(unittest.TestCase):
    """OR / AND / exclusion logic via get_items_by_selection."""

    def _run_selection(self, client, selection_cfg, fetch_side_effect):
        client._zotero_read.children.return_value = []
        with patch.object(
            client, "_fetch_items_for_tag", side_effect=fetch_side_effect
        ):
            return client.get_items_by_selection(selection_cfg, include_abstract=False)

    def test_or_operator_union(self):
        client, mock_zr = _make_client()
        mock_zr.children.return_value = []

        def fetch_side_effect(tag):
            if tag == "a":
                return {
                    "K1": _item_data("K1"),
                    "K2": _item_data("K2"),
                }
            if tag == "b":
                return {
                    "K2": _item_data("K2"),
                    "K3": _item_data("K3"),
                }
            return {}

        selection = TagSelectionConfig(
            include=TagRuleConfig(values=["a", "b"], operator="or"),
        )
        discovered, _ = self._run_selection(client, selection, fetch_side_effect)
        keys = {item.key for item in discovered}
        self.assertEqual(keys, {"K1", "K2", "K3"})

    def test_and_operator_intersection(self):
        client, mock_zr = _make_client()
        mock_zr.children.return_value = []

        def fetch_side_effect(tag):
            if tag == "a":
                return {
                    "K1": _item_data("K1"),
                    "K2": _item_data("K2"),
                }
            if tag == "b":
                return {
                    "K2": _item_data("K2"),
                    "K3": _item_data("K3"),
                }
            return {}

        selection = TagSelectionConfig(
            include=TagRuleConfig(values=["a", "b"], operator="and"),
        )
        discovered, _ = self._run_selection(client, selection, fetch_side_effect)
        keys = {item.key for item in discovered}
        self.assertEqual(keys, {"K2"})

    def test_exclusion_removes_items(self):
        client, mock_zr = _make_client()
        mock_zr.children.return_value = []

        def fetch_side_effect(tag):
            if tag == "a":
                return {
                    "K1": _item_data("K1", tags=[{"tag": "done"}]),
                    "K2": _item_data("K2"),
                }
            return {}

        selection = TagSelectionConfig(
            include=TagRuleConfig(values=["a"], operator="or"),
            exclude=TagRuleConfig(values=["done"], operator="or"),
            conflict_resolution="exclude_wins",
        )
        discovered, stats = self._run_selection(client, selection, fetch_side_effect)
        keys = {item.key for item in discovered}
        self.assertEqual(keys, {"K2"})
        self.assertEqual(stats.excluded_count, 1)


class TestGetItemsByTagLegacyPath(unittest.TestCase):
    """Legacy get_items_by_tag path reuses _fetch_items_for_tag."""

    def test_reuses_fetch_items_for_tag(self):
        client, mock_zr = _make_client()
        mock_zr.children.return_value = []
        with patch.object(
            client,
            "_fetch_items_for_tag",
            return_value={"K1": {"title": "T", "tags": []}},
        ) as mock_fetch:
            result = client.get_items_by_tag("millefeuille")

        self.assertTrue(len(result) > 0)
        mock_fetch.assert_called_once_with("millefeuille")

    def test_propagates_zotero_auth_error(self):
        client, _ = _make_client()
        auth_error = ZoteroAuthError("auth", None)
        with (
            patch.object(
                client,
                "_fetch_items_for_tag",
                side_effect=auth_error,
            ),
            self.assertRaises(ZoteroAuthError) as cm,
        ):
            client.get_items_by_tag("millefeuille")
        self.assertIs(cm.exception, auth_error)

    def test_propagates_zotero_api_error(self):
        client, _ = _make_client()
        api_error = ZoteroAPIError("api", None)
        with (
            patch.object(
                client,
                "_fetch_items_for_tag",
                side_effect=api_error,
            ),
            self.assertRaises(ZoteroAPIError) as cm,
        ):
            client.get_items_by_tag("millefeuille")
        self.assertIs(cm.exception, api_error)


if __name__ == "__main__":
    unittest.main()
