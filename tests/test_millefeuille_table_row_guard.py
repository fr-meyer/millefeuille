"""Synthetic table row preservation around the lint-only closing-row guard."""

import unittest

from markdown_it.token import Token

from millefeuille.domain.table_converter import _extract_table_rows


def _cell(value):
    text = Token("text", "", 0)
    text.content = value
    inline = Token("inline", "", 0)
    inline.children = [text]
    return [Token("td_open", "td", 1), inline, Token("td_close", "td", -1)]


def _rows(body):
    tokens = [Token("table_open", "table", 1), *body, Token("table_close", "table", -1)]
    return _extract_table_rows(tokens, 0, len(tokens) - 1)


class TableRowClosingGuardTests(unittest.TestCase):
    def test_empty_closing_rows_are_ignored(self):
        self.assertEqual(_rows([Token("tr_close", "tr", -1)]), [])

    def test_repeated_row_closures_do_not_duplicate_values(self):
        body = [
            *_cell("first"),
            Token("tr_close", "tr", -1),
            Token("tr_close", "tr", -1),
            *_cell("second"),
            Token("tr_close", "tr", -1),
        ]
        self.assertEqual(_rows(body), [["first"], ["second"]])

    def test_uneven_rows_keep_exact_values_and_padding(self):
        body = [
            *_cell("Amount"),
            *_cell("Unit"),
            Token("tr_close", "tr", -1),
            *_cell("1,234.50"),
            Token("tr_close", "tr", -1),
        ]
        self.assertEqual(_rows(body), [["Amount", "Unit"], ["1,234.50", ""]])

    def test_empty_cell_is_a_real_row(self):
        self.assertEqual(_rows([*_cell(""), Token("tr_close", "tr", -1)]), [[""]])

    def test_unterminated_last_row_is_preserved(self):
        self.assertEqual(_rows(_cell("synthetic tail")), [["synthetic tail"]])
