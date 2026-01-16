/**
 * Rounded Text Box Generator
 * Uses @remotion/rounded-text-box to create TikTok-style text boxes
 *
 * Usage: node rounded_text_box.js '<json_input>'
 * Input JSON: { lines: [{width, height}], borderRadius, horizontalPadding, textAlign }
 * Output JSON: { path, width, height, boundingBox }
 */

const { createRoundedTextBox } = require('@remotion/rounded-text-box');

// Read input from command line argument
const input = JSON.parse(process.argv[2]);

const {
    lines,           // Array of {width, height} for each line
    borderRadius = 20,
    horizontalPadding = 40,
    textAlign = 'center'
} = input;

// Create the rounded text box
const result = createRoundedTextBox({
    textMeasurements: lines,
    borderRadius: borderRadius,
    horizontalPadding: horizontalPadding,
    textAlign: textAlign
});

// Output the result as JSON
console.log(JSON.stringify({
    path: result.d,
    instructions: result.instructions,
    width: result.boundingBox.width,
    height: result.boundingBox.height,
    boundingBox: result.boundingBox
}));
