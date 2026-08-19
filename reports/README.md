# Put your PDFs here

Drop the credit report PDFs you want to process into this folder, then start
the app (`start.bat`, or `python app.py`) and click **Run**.

You can also point the app at any other folder using the **Browse…** button,
or on the command line:

```bash
python main.py "C:\some\other\folder"
```

Two things to know:

- Only PDFs sitting **directly** in the folder are read — files in sub-folders
  are ignored.
- PDFs in this folder are not committed to git (see `.gitignore`), so your
  documents stay private to your machine.
