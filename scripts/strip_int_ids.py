import re, glob

replacements = [
    (r"int\(user_id\) if isinstance\(user_id, str\) else user_id", "user_id"),
    (r"int\(user_id_int\) if isinstance\(user_id_int, str\) else user_id_int", "user_id_int"),
    (r"int\(customer_id\) if isinstance\(customer_id, str\) else customer_id", "customer_id"),
    (r"int\(customer_id\)", "customer_id"),
    (r"int\(user_id\)", "user_id"),
    (r"int\(inventory_item_id\)", "inventory_item_id"),
    (r"int\(data\['mechanic_id'\]\)", "data['mechanic_id']"),
    (r"int\(item_id\)", "item_id"),
    (r"int\(service_id\)", "service_id"),
    (r"int\(vehicle_type_id\)", "vehicle_type_id"),
    (r"int\(job_order_id\)", "job_order_id"),
    (r"int\(appointment_id\)", "appointment_id"),
]

for f in glob.glob("routes/*.py"):
    s = open(f, encoding="utf-8").read()
    for pat, rep in replacements:
        s = re.sub(pat, rep, s)
    open(f, "w", encoding="utf-8").write(s)
print("done")
