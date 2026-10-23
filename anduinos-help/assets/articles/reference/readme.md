# AnduinOS Documentation

Welcome to the official documentation repository for **AnduinOS**!

This repository contains the Markdown source files that power the AnduinOS documentation website.

## 📖 View the Documentation

If you are looking for guides, tutorials, or troubleshooting steps for AnduinOS, please visit our official documentation website:

**👉 [https://docs.anduinos.com/](https://docs.anduinos.com/)**

The website is the best way to browse, search, and read the documentation in a user-friendly format.

## 🛠️ Contributing

We welcome contributions from the community! If you find a typo, outdated information, or want to add a new guide, feel free to submit a Merge Request.

### CI/CD and Linting

To maintain high-quality documentation, this repository enforces strict CI/CD rules:
- **`check-links.py`**: A custom Python linter that runs on every check-in. It ensures:
  - All internal links and images are valid (No broken links).
  - No orphaned or unused images exist in the repository.
  - No empty or stub documents are published.
  - All images must include valid `alt` text for accessibility.
  - Corrupted (0-byte) images are automatically rejected.

*Note: If you are adding images, please place them in an `images/` subdirectory within your document's chapter folder to keep the repository organized.*

## 📜 License

This project is open-source and licensed under the **GNU General Public License v3.0 (GPL-3.0)**.

You may copy, distribute, and modify the documents as long as you track changes/dates in source files. Any modifications must also be made available under the GPL.

See the [LICENSE](LICENSE) file for more details.
