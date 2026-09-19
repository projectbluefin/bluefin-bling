// One owner of the GNOME-ESM shim every behavior harness needs.
//
// GNOME Shell sources import from `gi://` and `resource:///org/gnome/shell/...`,
// which plain node cannot resolve. A harness therefore reads the shipped source,
// rewrites *only* its GNOME import lines into bindings taken from a globalThis
// stub namespace, and imports the rewritten text as a `data:` module. Everything
// below the import block — the logic under test — stays byte-for-byte shipped
// source.
//
// That mechanism used to be reimplemented in each harness. The copies drifted:
// one learned that `import { gettext as _ }` must become `{ gettext: _ }` when
// it turns into a destructuring binding, and the other did not. Keeping the
// grammar here means the next import form GNOME introduces is a one-file change.
//
// Each harness declares *which* stubs it binds; none of them restate *how* the
// rewrite works.

import {readFileSync} from 'node:fs';

// Built fresh per call: these are `/g` regexes, and a shared instance carries
// `lastIndex` between `.test()` calls, which silently makes the second probe of
// the same source fail.
function gnomeImportPattern() {
    return /^\s*import\s+(?:(\*\s+as\s+\w+)|(\{[^}]*\})|(\w+))\s+from\s+['"](?:gi:\/\/|resource:\/\/\/)[^'"]+['"];?\s*$/gm;
}

/**
 * Rewrite every `gi://` / `resource:///` import in `source` into a const binding
 * drawn from `stubsExpression` (e.g. `'globalThis.__pscStubs'`).
 */
export function rewriteGnomeImports(source, stubsExpression) {
    return source.replace(
        gnomeImportPattern(),
        (_line, namespaceImport, namedImport, defaultImport) => {
            if (namespaceImport) {
                const name = namespaceImport.split(/\s+as\s+/)[1];
                return `const ${name} = ${stubsExpression}.${name};`;
            }
            if (namedImport) {
                // `{ gettext as _ }` is import syntax; destructuring needs
                // `{ gettext: _ }`, and `as` here would be a SyntaxError.
                const pattern = namedImport.replace(/\s+as\s+/g, ': ');
                return `const ${pattern} = ${stubsExpression};`;
            }
            return `const ${defaultImport} = ${stubsExpression}.${defaultImport};`;
        },
    );
}

/** Encode module text as a `data:` URL that `import()` accepts. */
export function asDataModule(source) {
    return `data:text/javascript;base64,${Buffer.from(source, 'utf8').toString('base64')}`;
}

/**
 * Read a GNOME source file, rewrite its GNOME imports against `stubsExpression`,
 * and import it.
 *
 * `rewrite` is an optional second pass for imports this module does not own —
 * a relative `./toggle.js`, say, which cannot resolve from a `data:` URL.
 * Throws when the source has no GNOME imports at all, since that means the
 * harness is rewriting a file that no longer looks the way it assumes.
 */
export function loadGnomeModule({path, stubsExpression, rewrite}) {
    const source = readFileSync(path, 'utf8');
    if (!gnomeImportPattern().test(source))
        throw new Error(`no gi:// or resource:/// imports found in ${path} — harness rewrite is stale`);

    let rewritten = rewriteGnomeImports(source, stubsExpression);
    if (rewrite)
        rewritten = rewrite(rewritten);
    return import(asDataModule(rewritten));
}
