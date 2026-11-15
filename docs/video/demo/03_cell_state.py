from paradoxdb import open_table

first, second, third = list(open_table("demo_tables/ASSETS.DB").records())[:3]

qty = first["Qty"]         # encoded zero: a valid 0
day = first["Day"]         # all-zero bytes: NULL
bad = third["Amount"]      # BCD with an illegal nibble: INVALID

for label, cell in (("Qty", qty), ("Day", day), ("Amount", bad)):
    print(f"{label:<7} state={cell.state.value:<8} value={cell.value!r:<6} "
          f"reason={cell.reason!r} raw={cell.raw_bytes.hex()}")
