# WPS Office

WPS Office is a suite of office software developed by Kingsoft Office. It is available for Windows, Linux, Android, and iOS.

## Flatpak install (Recommended)

You can install WPS Office via Flatpak by running the following commands in your terminal:

```bash
flatpak install flathub com.wps.Office
```

!!! warning "This package is global version"

    This package is the global version. If you want the Chinese edition, see [WPS Office Chinese edition](#wps-office-chinese-edition).

## System install (manual)

To install WPS Office on AnduinOS, you can run:

<!-- The link needs to be updated regularly. -->

```bash
wget https://wdl1.pcfg.cache.wpscdn.com/wpsdl/wpsoffice/download/linux/11723/wps-office_11.1.0.11723.XA_amd64.deb -O wps.deb
sudo apt install ./wps.deb -y
rm wps.deb
```

!!! warning "The link above may be outdated"

    The link above may be outdated. Please visit the [official website](https://www.wps.com/office/linux/) to get the latest version.

!!! warning "Unable to automatically upgrade this application"

    The above command only installs the launcher. If you run `sudo apt upgrade`, it won't upgrade it automatically. You will need to manually rerun the above command to upgrade.

    This is because the software provider didn't setup a repository for automatic updates. You will need to check the official website for updates.

## WPS Office Chinese edition

如果您需要原生中文界面以及 WPS AI 等特有功能，可以通过以下步骤手动下载并安装官方中文版：

1. 在浏览器中访问官网：[linux.wps.cn](https://linux.wps.cn/)。
2. 点击 **立即下载**，然后选择 **64位 Deb格式 (For X64)** 下载 `.deb` 安装包。
3. 打开终端，进入您的下载目录（例如 `~/Downloads`），然后运行以下命令进行安装：

```bash
sudo apt install ./wps-office_*_amd64.deb -y
```

## Fix the `missing fonts` issue

WPS may alert you that some fonts are missing. To fix this, you can install the fonts via this [GitHub Repo https://github.com/dv-anomaly/ttf-wps-fonts](https://github.com/dv-anomaly/ttf-wps-fonts).
