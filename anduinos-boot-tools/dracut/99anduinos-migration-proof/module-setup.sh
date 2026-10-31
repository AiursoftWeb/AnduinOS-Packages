#!/bin/bash

check() {
    return 0
}

depends() {
    return 0
}

install() {
    inst_hook pre-pivot 99 "$moddir/anduinos-migration-proof.sh"
}
