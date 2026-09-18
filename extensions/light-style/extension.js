/* extension.js
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation, either version 2 of the License, or (at your option)
 * any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program. If not, see <http://www.gnu.org/licenses/>.
 *
 * SPDX-FileCopyrightText: 2023 Florian Müllner <fmuellner@gnome.org>
 * SPDX-FileCopyrightText: 2026 Project Bluefin
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class LightStyleExtension extends Extension {
    enable() {
        // Capture the session's own value before the first _sync() overwrites
        // it. sessionMode.colorScheme is not a constant: _loadMode() copies it
        // out of /usr/share/gnome-shell/modes/*.json, so an image shipping a
        // custom session mode — which is exactly what Bluefin and Dakota do —
        // has a value here that the stock default does not. Restoring a literal
        // destroys it, and stomps any other theming extension that is also
        // driving this property.
        this._savedColorScheme = Main.sessionMode.colorScheme;

        this._interfaceSettings = new Gio.Settings({
            schema_id: 'org.gnome.desktop.interface',
        });

        this._colorSchemeId = this._interfaceSettings.connect(
            'changed::color-scheme',
            () => this._sync()
        );

        this._sync();
    }

    _sync() {
        const scheme = this._interfaceSettings.get_string('color-scheme');
        const isDark = scheme === 'prefer-dark';

        if (isDark) {
            Main.uiGroup.remove_style_class_name('light-style-active');
            Main.sessionMode.colorScheme = this._savedColorScheme;
        } else {
            Main.uiGroup.add_style_class_name('light-style-active');
            Main.sessionMode.colorScheme = 'prefer-light';
        }

        St.Settings.get().notify('color-scheme');
    }

    disable() {
        if (this._colorSchemeId) {
            this._interfaceSettings.disconnect(this._colorSchemeId);
            this._colorSchemeId = null;
        }
        this._interfaceSettings = null;

        Main.uiGroup.remove_style_class_name('light-style-active');
        Main.sessionMode.colorScheme = this._savedColorScheme;
        this._savedColorScheme = null;
        St.Settings.get().notify('color-scheme');
    }
}
