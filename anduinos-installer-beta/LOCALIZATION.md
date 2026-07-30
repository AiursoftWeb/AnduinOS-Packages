# Installer localization

The installer presents 28 official language and region choices. American
English (`en_US`) is the source language; the other 27 choices have GNU
gettext catalogs under `po/`.

| Code | Locale | Display name |
| --- | --- | --- |
| `ar` | `ar_SA.UTF-8` | العربية |
| `zh_CN` | `zh_CN.UTF-8` | 中文(简体) |
| `zh_HK` | `zh_HK.UTF-8` | 中文 (香港) |
| `zh_TW` | `zh_TW.UTF-8` | 中文(繁體) |
| `da` | `da_DK.UTF-8` | Dansk |
| `nl` | `nl_NL.UTF-8` | Nederlands |
| `en_US` | `en_US.UTF-8` | English (United States) |
| `en_GB` | `en_GB.UTF-8` | English (United Kingdom) |
| `fi` | `fi_FI.UTF-8` | Suomi |
| `fr` | `fr_FR.UTF-8` | Français |
| `de` | `de_DE.UTF-8` | Deutsch |
| `el` | `el_GR.UTF-8` | Ελληνικά |
| `hi` | `hi_IN.UTF-8` | हिन्दी |
| `id` | `id_ID.UTF-8` | Bahasa Indonesia |
| `it` | `it_IT.UTF-8` | Italiano |
| `ja` | `ja_JP.UTF-8` | 日本語 |
| `ko` | `ko_KR.UTF-8` | 한국어 |
| `pl` | `pl_PL.UTF-8` | Polski |
| `pt` | `pt_PT.UTF-8` | Português |
| `pt_BR` | `pt_BR.UTF-8` | Português do Brasil |
| `ro` | `ro_RO.UTF-8` | Română |
| `ru` | `ru_RU.UTF-8` | Русский |
| `es` | `es_ES.UTF-8` | Español |
| `sv` | `sv_SE.UTF-8` | Svenska |
| `th` | `th_TH.UTF-8` | ภาษาไทย |
| `tr` | `tr_TR.UTF-8` | Türkçe |
| `uk` | `uk_UA.UTF-8` | Українська |
| `vi` | `vi_VN.UTF-8` | Tiếng Việt |

The gettext domain is `anduinos-installer-beta`. UI code translates against
the language selected inside the installer instead of the process-global
locale, because users can change language on the welcome page without
restarting the application.

`compile-locales.sh` is both the catalog compiler and a release gate. It
rejects a language matrix that differs from the list above, untranslated
entries, invalid format placeholders, and catalogs that cannot be compiled.
APKG runs it before packaging and installs the generated catalogs below
`/usr/share/locale`.

Raw command output remains unchanged in the Output view so that copied logs
match command-line diagnostics and can be searched reliably. Installer-owned
page text, decisions, warnings, progress labels, and completion instructions
are localized.
