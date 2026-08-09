import claude_drive as cd

st = cd.read_state()
names = [n['n'] for n in st.get('nearby', [])]
print("pos:", st.get('pos'))
print("has flint:", 'flint' in names)
print("has flint(ground):", any(g['n'] == 'flint' for g in st.get('ground_items', [])))
print("names:", sorted(set(names)))
print("item_counts:", st.get('item_counts'))
print("items:", st.get('items'))
print("equipped:", st.get('equipped'))
print("ground_items:", st.get('ground_items'))
print("grass entries:", [n for n in st.get('nearby', []) if n['n'] == 'grass'])
print("scan:", st.get('scan'))
print("hunger:", st.get('hunger'))
