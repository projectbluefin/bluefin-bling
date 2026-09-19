// Harness for tests/gnome_module_loader.mjs — the shared GNOME-ESM shim.
//
// Every other harness in this suite depends on this module, so its import
// grammar is exercised directly here against synthetic sources rather than only
// through whichever import forms the three shipped extensions happen to use
// today.
//
// Usage: node gnome_module_loader_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {asDataModule, loadGnomeModule, rewriteGnomeImports} from './gnome_module_loader.mjs';

const STUBS = 'globalThis.__loaderStubs';

async function withSourceFile(source, callback) {
    const dir = mkdtempSync(join(tmpdir(), 'gnome-loader-'));
    try {
        const path = join(dir, 'subject.js');
        writeFileSync(path, source, 'utf8');
        return await callback(path);
    } finally {
        rmSync(dir, {recursive: true, force: true});
    }
}

const scenarios = {
    // Rewrite only: returns the text handed to import(), so the generated
    // bindings can be asserted without executing them.
    rewrite({source}) {
        return {rewritten: rewriteGnomeImports(source, STUBS)};
    },

    // Full path: rewrite, data: module, import, then report what the module saw
    // through its (stubbed) GNOME imports. The subject must export `probe()`.
    async load({source, stubs = {}, rewriteToggleImport = false}) {
        globalThis.__loaderStubs = stubs;
        const rewrite = rewriteToggleImport
            ? text => text.replace(/^\s*import\s+(\{[^}]*\})\s+from\s+['"]\.\/other\.js['"];?\s*$/gm,
                (_line, named) => `const ${named} = {fromOther: 'bound'};`)
            : undefined;
        return withSourceFile(source, async path => {
            const module = await loadGnomeModule({path, stubsExpression: STUBS, rewrite});
            return {probe: module.probe()};
        });
    },

    // A source with no GNOME imports must be refused, not silently imported:
    // that state means the harness is rewriting a file it no longer understands.
    async refuses({source}) {
        return withSourceFile(source, async path => {
            try {
                await loadGnomeModule({path, stubsExpression: STUBS});
            } catch (error) {
                return {threw: true, message: String(error.message)};
            }
            return {threw: false, message: null};
        });
    },

    // Loading twice in one process must behave identically both times; a shared
    // /g regex would carry lastIndex forward and fail the second call.
    async loadTwice({source, stubs = {}}) {
        globalThis.__loaderStubs = stubs;
        return withSourceFile(source, async path => {
            const first = await loadGnomeModule({path, stubsExpression: STUBS});
            const second = await loadGnomeModule({path, stubsExpression: STUBS});
            return {first: first.probe(), second: second.probe()};
        });
    },

    dataModule({source}) {
        const url = asDataModule(source);
        const prefix = 'data:text/javascript;base64,';
        return {
            hasPrefix: url.startsWith(prefix),
            decoded: Buffer.from(url.slice(prefix.length), 'base64').toString('utf8'),
        };
    },
};

const [, , scenarioName, optionsJson] = process.argv;
const scenario = scenarios[scenarioName];
if (!scenario) {
    console.error(`unknown scenario: ${scenarioName}`);
    process.exit(2);
}

const options = optionsJson ? JSON.parse(optionsJson) : {};
Promise.resolve(scenario(options))
    .then(result => {
        console.log(JSON.stringify(result));
    })
    .catch(error => {
        console.error(error?.stack ?? String(error));
        process.exit(1);
    });
