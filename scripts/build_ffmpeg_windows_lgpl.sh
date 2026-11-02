#!/usr/bin/env bash
# GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION.
# Rebuild the two affected FFmpeg DLLs; never changes an installed runtime.
# Requires GNU make, pkg-config, NASM, Meson/Ninja, CMake and Python3.
# Uses the pinned LLVM-MinGW UCRT toolchain, never a host MSVCRT default.
set -euo pipefail
if [ "$#" -ne 9 ]; then
  echo 'usage: build_ffmpeg_windows_lgpl.sh ffmpeg.tar.gz openh264.tar.gz nv-codec-headers.tar.gz dav1d.tar.gz svtav1.tar.gz zlib.tar.gz vendor-root output-dir llvm-mingw-ucrt.tar.xz' >&2
  exit 2
fi
ffmpeg_archive=$(realpath "$1")
openh264_archive=$(realpath "$2")
nvcodec_archive=$(realpath "$3")
dav1d_archive=$(realpath "$4")
svtav1_archive=$(realpath "$5")
zlib_archive=$(realpath "$6")
vendor=$(realpath "$7")
output=$(realpath -m "$8")
toolchain_archive=$(realpath "$9")
work=${EDITOR_FFMPEG_BUILD_ROOT:-/tmp/8k-editor-ffmpeg-lgpl8-ucrt}
mkdir -p "$work" "$output"
work=$(realpath "$work")
check() { printf '%s  %s\n' "$2" "$1" | sha256sum -c -; }
check "$ffmpeg_archive" 87a695bc61e16f4aa8437c2ab6c0bbe1ca572ceccec4a2e86013e36f49899b1e
check "$openh264_archive" 5e5efc74a0011f9853ccc3b1efeae92105dd5c5bea4af2432c6a9cc50e468007
check "$nvcodec_archive" 5e83af9e86efe7a1f4393ef342a299e5e7ca9f57fe210307afed3ae0f39d4a3f
check "$dav1d_archive" fa6c6ad6d9f57d86df90bc573c734c7342d4d3dc4fff1525f19344f6444c0b83
check "$svtav1_archive" 851250a3e5756eacfcd3e3368c8f25b4d1774205d5b7412768476d3788f4ed1f
check "$zlib_archive" af12bfe8e4d188ab7c1cd8a906e38ae578955a9f50ac4db36b5365d8d0cbaed5
check "$toolchain_archive" 55064b60b991a1e1e8090b8c2f84e82b22cfe5903d12c159d65064cf5e8ae335
extract() {
  local archive=$1 destination=$2
  mkdir -p "$destination"
  if [ ! -f "$destination/.editor-source-sha256" ]; then
    tar -xf "$archive" --strip-components=1 -C "$destination"
    sha256sum "$archive" | cut -d' ' -f1 > "$destination/.editor-source-sha256"
  else
    test "$(sha256sum "$archive" | cut -d' ' -f1)" = "$(cat "$destination/.editor-source-sha256")"
  fi
}
extract "$toolchain_archive" "$work/toolchain"
export PATH="$work/toolchain/bin:$PATH"
printf '#include <_mingw.h>\n#ifndef _UCRT\n#error UCRT_required\n#endif\nint probe;\n' | \
  x86_64-w64-mingw32-gcc -x c -c -o "$work/ucrt-probe.o" -
toolchain_pin=55064b60b991a1e1e8090b8c2f84e82b22cfe5903d12c159d65064cf5e8ae335
if [ -f "$work/.editor-toolchain-sha256" ]; then
  test "$(cat "$work/.editor-toolchain-sha256")" = "$toolchain_pin"
elif [ -d "$work/build-codecs" ] || [ -f "$work/openh264/libopenh264.a" ]; then
  echo 'Existing compiler objects have no matching toolchain identity. Choose a fresh EDITOR_FFMPEG_BUILD_ROOT.' >&2
  exit 2
else
  printf '%s\n' "$toolchain_pin" > "$work/.editor-toolchain-sha256"
fi
extract "$ffmpeg_archive" "$work/source"
extract "$openh264_archive" "$work/openh264"
extract "$nvcodec_archive" "$work/nvcodec"
extract "$dav1d_archive" "$work/dav1d"
extract "$svtav1_archive" "$work/svtav1"
extract "$zlib_archive" "$work/zlib"
prefix="$work/prefix"
mkdir -p "$prefix/lib/pkgconfig" "$prefix/include/wels"
make -C "$work/nvcodec" PREFIX="$prefix" install > "$output/nvcodec-install.log" 2>&1
make -C "$work/openh264" -j8 OS=mingw_nt ARCH=x86_64 CC=x86_64-w64-mingw32-gcc \
  CXX=x86_64-w64-mingw32-g++ AR=x86_64-w64-mingw32-ar ASM=nasm BUILDTYPE=Release \
  libopenh264.a > "$output/openh264-build.log" 2>&1
cp "$work/openh264/libopenh264.a" "$prefix/lib/"
cp "$work/openh264/codec/api/wels/"*.h "$prefix/include/wels/"
cat > "$prefix/lib/pkgconfig/openh264.pc" <<EOF
prefix=$prefix
libdir=\${prefix}/lib
includedir=\${prefix}/include
Name: OpenH264
Description: Pinned BSD-licensed OpenH264 static cross-build
Version: 2.6.0
Libs: -L\${libdir} -lopenh264
Libs.private:
Cflags: -I\${includedir}
EOF
cat > "$work/mingw-cross.txt" <<EOF
[binaries]
c = 'x86_64-w64-mingw32-gcc'
cpp = 'x86_64-w64-mingw32-g++'
ar = 'x86_64-w64-mingw32-ar'
strip = 'x86_64-w64-mingw32-strip'
pkgconfig = 'pkg-config'
windres = 'x86_64-w64-mingw32-windres'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
[properties]
needs_exe_wrapper = true
EOF
if [ ! -f "$work/dav1d-build/build.ninja" ]; then
  meson setup "$work/dav1d-build" "$work/dav1d" --cross-file="$work/mingw-cross.txt" \
    --prefix="$prefix" --libdir=lib --default-library=static --buildtype=release \
    -Denable_tools=false -Denable_tests=false -Denable_examples=false > "$output/dav1d-configure.log" 2>&1
fi
ninja -C "$work/dav1d-build" -j8 install > "$output/dav1d-build.log" 2>&1
cmake -S "$work/svtav1" -B "$work/svtav1-build" -G Ninja -DCMAKE_SYSTEM_NAME=Windows \
  -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++ \
  -DCMAKE_ASM_NASM_COMPILER=nasm -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DBUILD_APPS=OFF -DBUILD_TESTING=OFF \
  -DSVT_AV1_LTO=OFF -DNATIVE=OFF -DENABLE_AVX512=OFF -DEXCLUDE_HASH=ON > "$output/svtav1-configure.log" 2>&1
cmake --build "$work/svtav1-build" --parallel 8 > "$output/svtav1-build.log" 2>&1
cmake --install "$work/svtav1-build" >> "$output/svtav1-build.log" 2>&1
mkdir -p "$work/zlib-build"
(
  cd "$work/zlib-build"
  CHOST=x86_64-w64-mingw32 "$work/zlib/configure" --static --prefix="$prefix" > "$output/zlib-configure.log" 2>&1
  make -j8 > "$output/zlib-build.log" 2>&1
  make install >> "$output/zlib-build.log" 2>&1
)
mkdir -p "$work/build-codecs"
cd "$work/build-codecs"
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
export PKG_CONFIG_PATH=
configure_args=(--arch=x86_64 --target-os=mingw32 --cross-prefix=x86_64-w64-mingw32- --ld=x86_64-w64-mingw32-g++
 --enable-shared --disable-static --disable-autodetect --enable-version3 --disable-gpl --disable-nonfree
 --disable-programs --disable-doc --disable-debug --disable-network --disable-avdevice --disable-avfilter
 --disable-swscale --disable-swresample --disable-pthreads --enable-w32threads --disable-chromaprint
 --disable-libzvbi --enable-libopenh264 --enable-libdav1d --enable-libsvtav1 --enable-zlib --enable-ffnvcodec --enable-nvenc --disable-cuda-llvm
 --disable-cuvid --disable-nvdec --pkg-config=pkg-config --pkg-config-flags=--static
 '--extra-ldflags=-static-libgcc -static-libstdc++')
signature=$(printf '%s\n' "${configure_args[@]}" | sha256sum | cut -d' ' -f1)
if [ ! -f .editor-configured ] || [ "$(cat .editor-configured)" != "$signature" ]; then
  "$work/source/configure" "${configure_args[@]}" > "$output/configure.log" 2>&1
  printf '%s\n' "$signature" > .editor-configured
fi
# Preserve the exact pinned avutil ABI. No avutil implementation is rebuilt or
# linked statically into these DLLs. Its import library resolves plain avutil-60.
mkdir -p libavutil
cp "$vendor/lib/libavutil.dll.a" libavutil/
make_args=(-j8 -o libavutil/avutil.dll libavcodec/avcodec-62.dll libavformat/avformat-62.dll)
make "${make_args[@]}" > "$output/ffmpeg-build.log" 2>&1
for component in avcodec-62 avformat-62; do
  folder=${component%-*}
  cp "lib$folder/$component.dll" "$output/$component.dll"
  x86_64-w64-mingw32-strip --strip-unneeded "$output/$component.dll"
  x86_64-w64-mingw32-objdump -p "$output/$component.dll" > "$output/$component.pe.txt"
done
cp config.h config_components.h ffbuild/config.mak "$output/"
cp "$work/source/COPYING.LGPLv2.1" "$work/source/COPYING.LGPLv3" "$output/"
cp "$work/openh264/LICENSE" "$output/OpenH264-LICENSE"
python3 - "$output" "${configure_args[@]}" <<'PY'
import hashlib, json, pathlib, subprocess, sys
output=pathlib.Path(sys.argv[1])
components=[]
for name in ('avcodec-62.dll','avformat-62.dll'):
    path=output/name
    components.append(dict(component=name,output_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),output_bytes=path.stat().st_size))
receipt=dict(schema_version=1,source_commit='27b8d1a017de62b51c0f3a039327b5cc2d7ea13f',
 source_archive_sha256='87a695bc61e16f4aa8437c2ab6c0bbe1ca572ceccec4a2e86013e36f49899b1e',
 compiler=subprocess.check_output(['x86_64-w64-mingw32-gcc','--version'],text=True).splitlines()[0],
 assembler=subprocess.check_output(['nasm','-v'],text=True).strip(),
 crt='UCRT',toolchain=dict(name='llvm-mingw-20261020-ucrt-ubuntu-22.04-x86_64',sha256='55064b60b991a1e1e8090b8c2f84e82b22cfe5903d12c159d65064cf5e8ae335',url='https://github.com/mstorsjo/llvm-mingw/releases/download/20261020/llvm-mingw-20261020-ucrt-ubuntu-22.04-x86_64.tar.xz'),
 configure_args=sys.argv[2:],build_args=['-j8','-o','libavutil/avutil.dll','libavcodec/avcodec-62.dll','libavformat/avformat-62.dll'],
 external_sources=[dict(name='OpenH264',commit='cf568c83f71a18778f9a16e344effaf40c11b752',sha256='5e5efc74a0011f9853ccc3b1efeae92105dd5c5bea4af2432c6a9cc50e468007'),
 dict(name='nv-codec-headers',commit='876af32a202d0de83bd1d36fe74ee0f7fcf86b0d',sha256='5e83af9e86efe7a1f4393ef342a299e5e7ca9f57fe210307afed3ae0f39d4a3f'),
 dict(name='dav1d',commit='60507bffc0b13e7a81753a51005dbbeba4b23018',sha256='fa6c6ad6d9f57d86df90bc573c734c7342d4d3dc4fff1525f19344f6444c0b83'),
 dict(name='SVT-AV1',commit='4ae9272b588a05ee6e77a43e8dfdac05f54c4ff0',sha256='851250a3e5756eacfcd3e3368c8f25b4d1774205d5b7412768476d3788f4ed1f'),
 dict(name='zlib',commit='9f25df2c80c2b26c8b050fba880f0851d9d243ea',sha256='af12bfe8e4d188ab7c1cd8a906e38ae578955a9f50ac4db36b5365d8d0cbaed5')],
 omitted_external_libraries=['Chromaprint','FFTW','ZVBI'],components=components,
 recipe='scripts/build_ffmpeg_windows_lgpl.sh')
(output/'build-receipt.json').write_text(json.dumps(receipt,indent=2))
PY
echo "Rebuilt DLLs and provenance: $output"
