"""Group and total a small inventory, then print a report."""
from collections import defaultdict

ITEMS = [
    {"sku": "A1", "category": "tools", "qty": 4, "price": 12.5},
    {"sku": "B2", "category": "toys", "qty": 0, "price": 7.0},
    {"sku": "C3", "category": "tools", "qty": 10, "price": 3.25},
    {"sku": "D4", "category": "garden", "qty": 2, "price": 40.0},
    {"sku": "E5", "category": "toys", "qty": 6, "price": 9.99},
]


def group_by(items, key):
    groups = defaultdict(list)
    for item in items:
        groups[item[key]].append(item)
    return groups


def total(items):
    return round(sum(i["qty"] * i["price"] for i in items), 2)


def build_report(items, low_stock=3):
    lines = []
    for category, members in sorted(group_by(items, "category").items()):
        lines.append("%-8s %2d items  value %8.2f" % (category, len(members), total(members)))
        for item in sorted(members, key=lambda i: i["sku"]):
            flag = ""
            if item["qty"] == 0:
                flag = "  OUT"
            elif item["qty"] < low_stock:
                flag = "  low"
            lines.append("    %s x%-3d%s" % (item["sku"], item["qty"], flag))
    lines.append("total value %.2f" % total(items))
    return lines


if __name__ == "__main__":
    for line in build_report(ITEMS):
        print(line)
    in_stock = [i["sku"] for i in ITEMS if i["qty"]]
    print(in_stock, {c: len(m) for c, m in group_by(ITEMS, "category").items()})
RESULT = total(ITEMS)
