import json

profile = json.load(open("profile.json"))["tables"][0]
print(f"table {profile['table_name']}: declared {profile['declared_records']}, "
      f"found {profile['found_records']}, mismatch {profile['header_mismatch']:+d}, "
      f"blocks {profile['blocks']}")
print(f"{'field':<8}{'ok':>5}{'null':>6}{'invalid':>9}  sum / range")
for f in profile["fields"]:
    extra = f["sum"] or (f"{f['min']} .. {f['max']}" if f["min"] else "")
    print(f"{f['name']:<8}{f['ok']:>5}{f['null']:>6}{f['invalid']:>9}  {extra}")
print(f"sha256 {profile['sha256'][:32]}…")
