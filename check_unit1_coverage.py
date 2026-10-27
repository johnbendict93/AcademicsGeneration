import source_context as sc

ctx = sc.build_unit_context("AL3501", 1)
print(f"Total topics: {len(ctx.topics)}")
print(f"Gaps identified ({len(ctx.gaps)}):")
for g in ctx.gaps:
    print(f"  - {g}")

print("\nDetailed topic breakdown:")
for i, plan in enumerate(ctx.topics, start=1):
    print(f"  [{i}] {plan.status:<8} (ratio: {plan.match_ratio:.2f}, passages: {len(plan.passages):>2}) : {plan.topic}")