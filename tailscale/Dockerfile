ARG UPSTREAM_IMAGE
FROM ${UPSTREAM_IMAGE}

ARG OS_SECURITY_UPDATE_REVISION
RUN test -n "${OS_SECURITY_UPDATE_REVISION}" \
    && apk --no-cache upgrade
LABEL org.private-onyx.os-security-update-revision=${OS_SECURITY_UPDATE_REVISION}
