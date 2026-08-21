# OOBE Pages and Display Conditions

The OOBE currently contains up to 15 pages. Their order and initial display
conditions are defined by `OobeWindow._get_page_factories()` in
`assets/anduinos-oobe`.

| Order | Page | Display condition | After choosing "Continue Offline" |
| ---: | --- | --- | --- |
| 1 | Welcome Home | Always shown | Kept |
| 2 | Connect to the Internet | Shown when startup connectivity is not `FULL`, including no connection, local-only or limited connectivity, a captive portal, or a detection error | Kept as the entry point for choosing whether to connect or continue offline |
| 3 | Define Your Visual Order | Always shown | Kept |
| 4 | Digital Sovereignty, Under Your Control | Always shown | Kept |
| 5 | Keep Your System Up to Date | Always added initially | Removed |
| 6 | Secure Boot Configuration | Shown when the Secure Boot status is `ENABLED` or `UNKNOWN` | Kept |
| 7 | Configure Hardware Drivers? | Always shown | Kept |
| 8 | USTC Flathub Mirror for China | Shown when `LANGUAGE` (preferred) or `LANG` starts with `zh_CN` | Removed |
| 9 | Run Windows Apps, with Ease (Bottles) | Shown when the CPU architecture is not `aarch64` | Removed |
| 10 | The Magic at Your Fingertips (Shortcuts) | Always shown | Kept |
| 11 | Productive From Day One (App Recommendations) | Always added initially | Removed |
| 12 | Connect & Protect Your Data (Accounts and Backup) | Always added initially | Removed |
| 13 | Your Data. Your Rules. Period. (Privacy) | Always shown | Kept |
| 14 | Welcome to the AnduinOS Community | Always shown | Kept |
| 15 | All Set | Always shown | Kept |

## Condition Details

- The Secure Boot page is omitted when the status is `DISABLED` or
  `UNSUPPORTED`. It is also omitted if inspection raises an exception. It is
  shown only when Secure Boot is enabled or its status is unknown.
- The hardware drivers page is always shown immediately after the optional
  Secure Boot page. It does not inspect hardware, virtualization, connectivity,
  or driver state. It only opens AnduinOS Driver Center or lets the user skip.
- The China mirror page currently recognizes only `zh_CN`, not `zh_TW` or
  `zh_HK`.
- The Bottles page does not use the Bottles installation state as a display
  condition. When Bottles is already installed, the page remains visible but
  offers a "Configure Bottles" action instead of installation.
- The Bottles architecture check currently excludes only `aarch64`; other
  non-x86 architectures may still see the page.
- After the user chooses "Continue Offline", every page marked with
  `page._requires_internet = True` is removed from the carousel.
- Most conditions are evaluated once when the window is created. Pages are not
  necessarily added or removed dynamically when hardware, locale, or network
  state changes later.

## Related Code

- Page order and initial conditions: `OobeWindow._get_page_factories()`
- Offline page removal: `OobeWindow._continue_offline()`
- Network readiness detection: `internet_connection_ready()`
- CPU architecture detection: `is_arm64()`
- Chinese locale detection: `is_chinese_locale()`
