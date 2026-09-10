ARG UPSTREAM_IMAGE
FROM ${UPSTREAM_IMAGE}

ARG OS_SECURITY_UPDATE_REVISION
COPY onyx/code-interpreter/upgrade-os.sh /tmp/private-onyx-upgrade-os.sh
RUN test -n "${OS_SECURITY_UPDATE_REVISION}" \
    && sh /tmp/private-onyx-upgrade-os.sh \
    && rm /tmp/private-onyx-upgrade-os.sh
LABEL org.private-onyx.os-security-update-revision=${OS_SECURITY_UPDATE_REVISION}
