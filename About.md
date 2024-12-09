# About

## What is it?

The Info Repository is the core of a [multi-repository project](https://github.com/alsosee/finder/wiki) designed to catalog and interlink information about movies, books, people, and more.
Each entity is represented by a single YAML file, grouped into directories based on type and further organized (e.g., movies by release year under `Movie/YYYY/`).

The content of this repository serves as the structured data source for generating a user-friendly [website](https://alsosee.info) through the accompanying [`finder` repository](https://github.com/alsosee/finder).
The website offers a browsing experience similar to MacOS Finder, designed to encourage exploration of interlinked entities. For example:

A movie file may reference actors, directors, or writers.
The person file for an actor will, in turn, display connections to movies they starred in.
This interconnected structure makes it easy to navigate and explore relationships across entities.

## Why is this Different from Wikipedia or IMDb?

In essence, this project isn’t meant to replicate or compete with Wikipedia or IMDb — it’s a flexible, developer-friendly foundation that encourages experimentation, exploration, and collaborative data curation.

## Contributing

We welcome contributions! Here are some ways you can help:

1. Add Information: Submit YAML files for new movies, books, people, or other supported entities.
2. Update or Improve: Correct errors or enrich existing files with additional details or references.
3. Suggest Features: Share ideas for enhancing the repository's structure or improving the website experience.
