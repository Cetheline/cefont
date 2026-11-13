# README artwork and specimens

The project README combines original terminal screenshots, illustrative artwork,
and real font-rendering panels. The illustrations are not screenshots of a
graphical CeFont app.

## Original screenshot evidence

Five unedited user-provided terminal captures are saved in [evidence/](evidence/README.md).
The pangram, waterfall, and comparison captures replace the corresponding
rendered specimen panels in the main README. Wrapped text and ASCII glyph
captures are available in an expandable gallery. Each has source provenance
and an original-file SHA-256 check. No generated changes were made to these
screenshots, and no artificial light-theme versions were created.

## Native panels

`render_specimens.py` produces light and dark variants of six panels:

- `cefont-waterfall-*.png`: Noto Sans Mono at five pixel sizes.
- `cefont-comparison-*.png`: Noto Sans Mono, Noto Sans, and Noto Serif.
- `cefont-charmap-*.png`: printable Basic Latin glyphs and codepoint labels.
- `cefont-metadata-*.png`: actual metadata extracted from the specimen file.
- `cefont-coverage-*.png`: measured coverage against bundled definitions.
- `cefont-pipeline-*.png`: an explanatory diagram of family-name previews.

To regenerate from the repository root:

```bash
python3 -B docs/assets/render_specimens.py
```

The script reads installed Noto regular fonts beneath `/usr/share/fonts` and
uses CeFont's FreeType renderer and metadata/coverage functions. It does not
install, remove, index, or alter those fonts. Use `--font-root` to select a
different font search directory, or `--output` for a disposable destination.
Metadata and coverage can differ between installed font versions. Font files
are not distributed with these images. The original `cefont-specimen-*.png`, waterfall, and comparison pairs are
retained as reference assets; the main README now uses the corresponding
original terminal captures instead.

## Generated illustrations

`cefont-type-workshop.webp` and `cefont-font-library.webp` were generated with
the built-in `image_gen` tool. The selected outputs were encoded as WebP at
quality 90 without changing their composition. Both images are 1672 by 941
pixels. The README labels them as illustrations and provides descriptive alt
text. The real application has a command-line interface.

Final generation prompts:

### Typographic workshop

> Use case: ads-marketing. Asset type: wide opening illustration for the CeFont GitHub README. Create a beautifully art-directed horizontal 16:9 typographic still life, not a software screenshot. A spacious charcoal-green background (#111c18), sculptural off-white and muted sage green letterforms Aa, a slashed zero, curly braces and square brackets arranged like physical metal type specimens on shallow architectural planes. Precise crafted 3D forms, subtle realistic matte texture, soft directional studio light, restrained shadows, strong left-to-right composition, generous breathing room. The character forms themselves are the subject, no extra text, no wordmark, no terminal window, no UI, no neon, no gradients, no purple, no tiny labels, no fake screenshots. Palette exclusively off-white, graphite-green, muted sage. Crisp refined editorial typography composition suitable for a Linux font management project, accessible silhouette contrast, polished and visually interesting at 1120px wide.

### Font library

> Use case: illustration-story. Asset type: wide conceptual illustration about organizing fonts for CeFont's GitHub README. Horizontal 16:9 art-directed isometric typographic library: three beautifully proportioned physical archival trays and upright off-white font specimen sheets, each sheet displaying only one large typographic character A, g, or a slashed zero. A small stack of sheets sits outside the trays. Muted sage-green sculptural frames and charcoal-green negative space, the same restrained palette #111c18, #74b896, ivory. Refined matte paper and powder-coated metal material, soft studio lighting and subtle shadows, crisp simple shapes, interesting asymmetric composition with very generous breathing room. This is a conceptual illustration of organizing typography, not a screenshot, not a dashboard, not a UI. No interface controls, no status dots, no buttons, no text labels, no logos, no neon, no purple, no orange. Elegantly crafted, clear silhouettes that read well when reduced to a README section image.
