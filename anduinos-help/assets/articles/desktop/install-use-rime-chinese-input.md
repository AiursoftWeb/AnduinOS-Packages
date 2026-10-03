# Type Chinese with Rime

AnduinOS uses Rime as its Chinese input method. Rime provides Pinyin input, learns from the candidates you select, and can mix Chinese and English in the same document.

On a new installation, the AnduinOS Installer offers Rime when you select Simplified Chinese and the package source is reachable. It is not installed on every system, so users who do not type Chinese do not receive the additional dictionaries and input-method packages.

## Install Rime when it is missing

If **Chinese (Rime)** is not available in Settings, install the AnduinOS configuration and the Rime engine:

```bash
sudo apt update
sudo apt install anduinos-rime
```

Log out and log back in after installation. This lets GNOME and IBus discover the new input method cleanly.

## Add the input source

1. Open **Settings**.
2. Select **Keyboard**.
3. Under **Input Sources**, select **Add Input Source**.
4. Add **Chinese (Rime)**.

English and Rime can remain in the list together.

![GNOME Keyboard settings with English and Chinese Rime input sources](images/rime/input-sources.png)

## Switch between English and Chinese

Press <kbd>Super</kbd> + <kbd>Space</kbd> to move to the next input source. The input indicator on the taskbar changes to show the active source.

You can also select the indicator directly. The Rime menu provides access to its current input mode, deployment, synchronization, and IBus settings.

![Rime selected from the input-source menu on the AnduinOS taskbar](images/rime/input-source-menu.png)

## Type with Pinyin

With **Chinese (Rime)** selected:

1. Type the Pinyin for the text you want.
2. Review the numbered candidate list.
3. Press the candidate number, or use the arrow keys and <kbd>Space</kbd>, to commit a candidate.
4. Use the arrows at the right side of the candidate window to view more candidates.

![Rime presenting Chinese candidates while Pinyin is entered in Text Editor](images/rime/pinyin-candidates.png)

Rime can produce normal Chinese text while retaining English words, letters, and numbers where they are appropriate. The AnduinOS package also includes candidate filtering that keeps useful spelling guidance without displaying internal Pinyin annotations beside every ordinary candidate.

![Chinese and Latin text entered together with Rime](images/rime/mixed-text.png)

## Customize or refresh Rime

Open the input-source menu and select **Customize IBus** for the graphical IBus preferences. If you edit Rime configuration files manually, select **Deploy** from the same menu to rebuild the active configuration.

Personal Rime configuration is stored under:

```text
~/.config/ibus/rime/
```

Files in this directory take precedence over the AnduinOS defaults. Package upgrades update the system defaults without intentionally replacing your personal configuration.

## Troubleshooting

### Chinese (Rime) is not listed

Confirm the package is installed:

```bash
dpkg-query -W anduinos-rime ibus-rime
```

If either package is missing, install `anduinos-rime`, then log out and back in.

### Super+Space does not switch input sources

Open **Settings** > **Keyboard** and confirm that both your physical keyboard layout and **Chinese (Rime)** appear under **Input Sources**. The same page shows the configured input-source shortcut.

### Candidates do not reflect a configuration change

Select **Deploy** from the Rime input-source menu. If applications still use the old configuration, save your work and log out once.
