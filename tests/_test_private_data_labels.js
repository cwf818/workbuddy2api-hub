/* #528: X-Private-Data 是「数据私有」声明，true = 不参与优化。
 *
 * 下拉文案曾把 true/false 写反：用户按文案选「允许参与」实际会发 true
 * （拒绝优化），想保护数据反而选了 false。这里钉住选项文案与英文词条。
 *
 *   node tests/_test_private_data_labels.js
 */
const assert = require('assert');
const {dashboardHtml} = require('./_dashboard_source.js');
const html = dashboardHtml();

assert.ok(/<option value="true">不参与模型优化 \(true\)<\/option>/.test(html),
          'true must mean do not optimize');
assert.ok(/<option value="false">允许参与模型优化 \(false\)<\/option>/.test(html),
          'false must mean allow optimization');
assert.ok(/'不参与模型优化 \(true\)': 'Do not train on this data \(true\)'/.test(html),
          'the English label for true is reversed');
assert.ok(/'允许参与模型优化 \(false\)': 'Allow model optimization \(false\)'/.test(html),
          'the English label for false is reversed');
console.log('private data labels assertions passed (4 checks)');
