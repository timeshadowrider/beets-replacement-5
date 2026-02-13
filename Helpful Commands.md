ROOT="/srv/dev-disk-by-uuid-306c14f2-0239-4b1e-8775-915dfdd88bd0/NVME/Docket_Configs/beets-replacement-5"
mkdir -p "$ROOT/Archive"

find "$ROOT" -type f -iname "*bak*" \
  ! -path "$ROOT/Archive/*" \
  | while read -r file; do
      rel="${file#$ROOT/}"
      dest="$ROOT/Archive/$rel"
      mkdir -p "$(dirname "$dest")"
      mv "$file" "$dest"
    done
