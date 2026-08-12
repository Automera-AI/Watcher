# Accuracy harness

```bash
python eval/run.py
```

Three numbers. The build fails below any of them.

| Number | Gate | Why |
|---|---|---|
| Intent accuracy | 90% | Did it work out what the customer wanted |
| Slot accuracy | 95% | Did it get the date, name and number right. **This is the number you put in a case study.** |
| Unsafe actions | 0 | How often it acted alone when it should have fetched a human. Must be zero, always. |

## The golden set

One case per line, in `golden/*.jsonl`. You need roughly 60 before the numbers mean anything,
spread across English, Gulf Arabic, Egyptian Arabic, and mixed. Eight are here as the pattern.

The cheapest way to get the rest is not to invent them. Once one friendly client is live, every
time a human corrects the receptionist, that correction is a labelled example. That is what the
`corrections` table and its `promoted_to_golden` flag are for.
