FROM mcr.microsoft.com/azurelinux/base/core:3.0
RUN printf 'private-build-and-pull\n' > /private-build-proof.txt
CMD ["/bin/sh", "-c", "cat /private-build-proof.txt; sleep infinity"]
