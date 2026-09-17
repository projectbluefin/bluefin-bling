/* Stub of `gi://GObject`. */

const GObject = {
	// GJS returns a constructible wrapper; the plain class is close enough for
	// everything toggle.js does with it.
	registerClass(...args) {
		return args[args.length - 1]
	},
	ParamSpec: {},
}

export default GObject
