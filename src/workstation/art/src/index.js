'use strict';
// Every piece of art the lane builds. Each item: { group, file, w, h, title, ref, rule, formats, preview, draw(canvas) }.
//   rule 'cde'   -> only CDE icon symbols (the 16 fixed icon colours + the 5 palette-following symbols)
//   rule 'brand' -> fixed colours (logos in their brand palette, Deep32 Library's lockup and plates)
function items() {
  return [
    ...require('./icons').items(),
    ...require('./backdrops').items(),
    ...require('./logos').items(),
    ...require('./library').items(),
  ];
}
module.exports = { items };
