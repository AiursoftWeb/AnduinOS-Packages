ARG SUITE
FROM ubuntu:${SUITE} AS upstream
ENV DEBIAN_FRONTEND=noninteractive
# Use the target image's own archive/ports selection, pockets and signing key.
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends dpkg-dev ca-certificates
WORKDIR /download
ARG GRUB_VERSION
RUN test -n "$GRUB_VERSION" && apt-get source --download-only "grub2=$GRUB_VERSION" \
    && sha256sum *.dsc *.tar.* > sources.sha256

FROM ubuntu:${SUITE} AS build
ENV DEBIAN_FRONTEND=noninteractive
COPY --from=upstream /download/ /build/download/
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential devscripts equivs python3 ca-certificates
WORKDIR /build
RUN dpkg-source -x download/*.dsc source \
    && cd source \
    && mk-build-deps --build-profiles nocheck --install --remove --tool 'apt-get -y -o Acquire::Retries=3 --no-install-recommends' debian/control
COPY --from=boot-architecture architecture.py /build/architecture.py
ENV DEB_BUILD_OPTIONS="parallel=4 nocheck"
COPY bootstrap.cfg build-payload.sh /build/
RUN bash /build/build-payload.sh
FROM scratch AS artifacts
COPY --from=build /payload/ /
