if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
    update-alternatives --remove \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth || true
    update-initramfs -u || true
fi
