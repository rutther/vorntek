from django.test import SimpleTestCase

from .customer_pool_queries import (
    normalize_source_filters,
    parse_country_filter,
    parse_value_bound,
)


class CustomerPoolQueryParsingTests(SimpleTestCase):
    def test_value_bound_rejects_non_finite_values_and_clamps_range(self):
        self.assertIsNone(parse_value_bound(''))
        self.assertIsNone(parse_value_bound('not-a-number'))
        self.assertIsNone(parse_value_bound('NaN'))
        self.assertIsNone(parse_value_bound('Infinity'))
        self.assertEqual(parse_value_bound('-2'), 0.0)
        self.assertEqual(parse_value_bound('31.26'), 31.3)
        self.assertEqual(parse_value_bound('999999'), 2000.0)

    def test_country_filter_normalizes_codes_and_preserves_legacy_name_terms(self):
        codes, terms = parse_country_filter(['ke', 'KE', ' cn ', '肯尼亚', '', '中国'])

        self.assertEqual(codes, ['KE', 'CN'])
        self.assertEqual(terms, ['肯尼亚', '中国'])

    def test_source_filters_fall_back_to_all_for_unknown_values(self):
        self.assertEqual(normalize_source_filters('research', 'file_import'), ('research', 'file_import'))
        self.assertEqual(normalize_source_filters('private_adapter', 'crawler'), ('all', 'all'))
