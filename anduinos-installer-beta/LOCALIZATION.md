# Installer localization

The installer presents 28 official language and region choices. American
English (`en_US`) is the source language; the other 27 choices have GNU
gettext catalogs under `po/`.

`data/languages.json` is the installer-owned regional policy source. Each
entry binds a language choice to its locale, representative timezone, default
physical XKB layout and optional input-method policy. The document also owns
the default language, locale aliases and the complete keyboard-layout chooser.
Input-method definitions declare the user-visible product and native-language
names, an optional desktop input source, packages, required target paths and
any user-default files to deploy. The executor does not know the names of Rime,
Mozc, Cangjie, Chewing, Hangul, IBus or any particular language. The installer
never reads or modifies
`/usr/share/language-selector/data/pkg_depends`.

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

The maintained recommendations are Rime for Simplified Chinese, Cangjie Big
for Hong Kong Traditional Chinese, Chewing for Taiwan Traditional Chinese,
Mozc for Japanese, Hangul for Korean, LibThai for Thai and Unikey for
Vietnamese. Each recommendation appears as a default-selected optional download
and is disabled while offline, symmetrically with system updates and online
driver discovery. Languages whose JSON entry has no recommendation do not show
the extra choice.

Before creating the account, the privileged installer configures the physical
keyboard offline and handles the selected input method independently. It can
reuse a complete payload already present on the installation medium or install
exactly the packages declared by the selected policy. Offline or download
failure is a visible warning and does not abort installation. Desktop-source
registration is generated generically from JSON. Input-method packages own
their shared defaults, so the installer never writes product configuration to
`/etc/skel` or an existing user's home. Adding a language, layout, locale alias
or input method is a data-only change to `data/languages.json` (plus the normal
gettext catalog when translating the installer UI into a new language).

Raw command output remains unchanged in the Output view so that copied logs
match command-line diagnostics and can be searched reliably. Installer-owned
page text, decisions, warnings, progress labels, and completion instructions
are localized.
