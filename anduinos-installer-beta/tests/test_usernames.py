import re
import unittest

from installer_core.username_policy import is_valid_username
from installer_core.usernames import suggest_username


class UsernameSuggestionTests(unittest.TestCase):
    def test_representative_names(self):
        cases = {
            "Anduin Xue": "anduin",
            "  Anduin   Xue  ": "anduin",
            "François Dupont": "francois",
            "张三": "zhangsan",
            "O’Connor Smith": "oconnor",
            "Jean-Pierre Dupont": "jeanpierre",
            "123 Anduin": "user",
            "😀": "user",
            "": "user",
        }
        for full_name, expected in cases.items():
            with self.subTest(full_name=full_name):
                self.assertEqual(suggest_username(full_name), expected)

    def test_normalizes_unicode_before_selecting_first_component(self):
        self.assertEqual(suggest_username("Ａｎｄｕｉｎ Xue"), "anduin")
        self.assertEqual(suggest_username("Anduin\tXue"), "anduin")

    def test_removes_leading_digits_and_invalid_characters(self):
        self.assertEqual(suggest_username("123Anduin Xue"), "anduin")
        self.assertEqual(suggest_username("_Alice Example"), "alice")

    def test_limits_every_suggestion_to_valid_ascii(self):
        for full_name in (
            "AnExtremelyLongGivenName Family",
            "François Dupont", "张三", "😀", "123Anduin",
        ):
            with self.subTest(full_name=full_name):
                self.assertRegex(
                    suggest_username(full_name),
                    re.compile(r"^[a-z][a-z0-9]{0,15}$"),
                )

    def test_long_full_name_produces_a_sixteen_character_username(self):
        self.assertEqual(
            suggest_username("AnExtremelyLongGivenName Family"),
            "anextremelylongg",
        )

    def test_custom_length_is_applied_after_transliteration(self):
        self.assertEqual(suggest_username("张三", max_length=5), "zhang")
        with self.assertRaises(ValueError):
            suggest_username("Alice", max_length=0)

    def test_reserved_names_produce_acceptable_suggestions(self):
        for full_name in ("Root User", "Live User", "Ubuntu User"):
            with self.subTest(full_name=full_name):
                suggestion = suggest_username(full_name)
                self.assertTrue(is_valid_username(suggestion))
                self.assertNotEqual(suggestion, full_name.split()[0].lower())


if __name__ == "__main__":
    unittest.main()
