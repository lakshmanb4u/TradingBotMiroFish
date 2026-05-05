#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")/../bookmap-recorder" && pwd)"
cd "$DIR"

echo "=== BookmapOrderflowRecorder Build ==="

# --- Discover Bookmap API jars ---
# Bookmap 7.7+ stores jars in /Contents/app/lib/
L1API="/Applications/Bookmap.app/Contents/app/lib/bm-l1api.jar"
SIMPLIFIED="/Applications/Bookmap.app/Contents/app/lib/bm-simplified-api-wrapper.jar"

if [[ ! -f "$L1API" ]]; then
    echo "ERROR: bm-l1api.jar not found at:"
    echo "  $L1API"
    echo ""
    echo "Please confirm Bookmap is installed at /Applications/Bookmap.app"
    exit 1
fi
if [[ ! -f "$SIMPLIFIED" ]]; then
    echo "ERROR: bm-simplified-api-wrapper.jar not found at:"
    echo "  $SIMPLIFIED"
    exit 1
fi

mkdir -p lib
cp "$L1API"     lib/
cp "$SIMPLIFIED" lib/
echo "Copied Bookmap API jars to lib/"

# --- Use Homebrew OpenJDK if available (macOS) ---
if [[ -d "/opt/homebrew/opt/openjdk@25" ]]; then
    export JAVA_HOME="/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home"
elif [[ -d "/opt/homebrew/opt/openjdk" ]]; then
    export JAVA_HOME="/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"
fi
if [[ -n "$JAVA_HOME" ]]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi

# Check for Java
if ! command -v javac &> /dev/null; then
    echo "ERROR: javac not found. Install JDK 11 or later (brew install openjdk)."
    exit 1
fi
echo "Using javac: $(which javac) ($(javac -version 2>&1 | head -1))"

# Compile targeting Java 21 (Bookmap's runtime)
mkdir -p build/classes
javac --release 21 -cp "lib/bm-l1api.jar:lib/bm-simplified-api-wrapper.jar" \
    -d build/classes \
    src/main/java/com/openclaw/BookmapOrderflowRecorder.java

# Build JAR
jar cf build/BookmapOrderflowRecorder.jar \
    -C build/classes com/openclaw/BookmapOrderflowRecorder.class

echo "=== Build complete ==="
echo "Output: $DIR/build/BookmapOrderflowRecorder.jar"
echo ""
echo "Install into Bookmap:"
echo '  cp build/BookmapOrderflowRecorder.jar "$HOME/Library/Application Support/Bookmap/Strategies/"'
