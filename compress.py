# Create backup and compress everything
mkdir backup_originals
find . -name "*.png" -exec cp {} backup_originals/ \; -exec optipng -o7 {} \;
find . -name "*.css" ! -name "*.min.css" -exec cp {} backup_originals/ \; -exec sh -c 'cleancss "$1" -o "$1"' _ {} \;
find . -name "*.js" ! -name "*.min.js" -exec cp {} backup_originals/ \; -exec sh -c 'terser "$1" -o "$1"' _ {} \;