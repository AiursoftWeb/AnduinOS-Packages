if [ "$1" = "configure" ]; then
    update-alternatives --install \
        /usr/share/plymouth/themes/default.plymouth \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth \
        150
    update-alternatives --set \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth || true
    update-initramfs -u || true
fi
