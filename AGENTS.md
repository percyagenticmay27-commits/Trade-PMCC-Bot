# Publishing changes from DigitalOcean

After a source change in this deployment is tested, publish the same reviewed
files to this project's public GitHub repository before marking the work
complete. Run the mirror command on demand for this project only. Repeat
`--path` for every changed file, including both paths when renaming a file.

```bash
python3 -u ~/.local/share/project-github-backups/project_backup.py --project Trade-PMCC-Bot --path path/to/changed-file
```

Confirm the output reports `BACKUP_OK` and verify the commit on GitHub. Review
changes for credentials, account information, runtime state, and captured data
before publishing. Resolve any unrelated local edits or GitHub divergence
before retrying. Use this workflow only when making an actual project change.
