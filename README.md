# MVC Runner

A minimum viable context tool for reducing token usage in LLMs.

## Structure

```
MVC_Runner/
├── comparator.py       # Line-by-line file comparator
├── examples/           # Sample files for a quick first run
│   ├── sample_a.txt
│   └── sample_b.txt
├── requirements.txt
└── README.md
```

## Comparator

Compares two files line by line and reports where they differ.

```bash
# Run with the bundled example files
python comparator.py

# Or compare your own files
python comparator.py path/to/file_a.txt path/to/file_b.txt
```

Exit code is `0` if the files are identical, `1` if differences were found.
