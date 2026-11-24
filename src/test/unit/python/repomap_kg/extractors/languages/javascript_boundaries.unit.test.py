import unittest
from urllib.parse import unquote
from common_lore_index.extractors.languages.javascript import extract_javascript_file_observations
from common_lore_index.extractors.languages.javascript_detection import (
    _has_express_marker,
    _has_jquery_marker,
    _detect_format,
)
from common_lore_index.extractors.languages.javascript_references import (
    _is_dynamic_literal,
    _is_secret_prone,
    _literal_type,
)

class JavascriptBoundariesUnitTests(unittest.TestCase):
    def test_javascript_detection_markers(self):
        self.assertTrue(_has_express_marker("const express = require('express');"))
        self.assertFalse(_has_express_marker("const fs = require('fs');"))
        self.assertTrue(_has_jquery_marker("$('#app').on('click', function() {});"))
        self.assertFalse(_has_jquery_marker("console.log('hello');"))
        self.assertEqual(_detect_format("app.ts"), "typescript")

    def test_javascript_imports_exports_and_routes(self):
        js_code = """
import express from 'express';
const auth = require('./middleware/auth');
const app = express();

app.get('/api/users', auth, (req, res) => {
    res.json({ users: [] });
});

app.post('/api/users', (req, res) => {
    res.status(201).send('created');
});

export default app;
"""
        obs = extract_javascript_file_observations("src/server.ts", js_code)
        self.assertEqual(obs[0].kind, "js.file")
        kinds = {o.kind for o in obs}
        self.assertIn("js.module", kinds)

    def test_malformed_and_bracketed_ipv6_imports_keep_distinct_safe_targets(self):
        observations = extract_javascript_file_observations(
            "fixture.mjs",
            "import 'https://example.invalid:not-a-port/path';\n"
            "import 'https://[2001:db8::1]:8443/path?token=fixture-value';",
        )
        imports = [item for item in observations if item.kind == "js.import"]
        self.assertEqual(len(imports), 2)
        self.assertTrue((imports[0].target or "").startswith("unknown:"))
        self.assertTrue((imports[1].target or "").startswith("external.url:"))
        self.assertIn("[2001:db8::1]:8443", unquote(imports[1].target or ""))
        self.assertNotIn("not-a-port", str([item.to_dict() for item in observations]))
        self.assertNotIn("fixture-value", str([item.to_dict() for item in observations]))

    def test_javascript_reference_helpers(self):
        self.assertTrue(_is_dynamic_literal("`template_${val}`"))
        self.assertFalse(_is_dynamic_literal('"static_string"'))
        self.assertTrue(_is_secret_prone("api_token"))
        self.assertFalse(_is_secret_prone("user_name"))
        self.assertEqual(_literal_type('"hello"'), "string")
        self.assertEqual(_literal_type("123"), "integer")

    def test_javascript_nestjs_routes(self):
        nest_code = """
import { Controller, Get, Post, Body, Param, Injectable } from '@nestjs/common';

@Injectable()
export class UserService {}

@Controller('users')
export class UserController {
    @Get(':id')
    findOne(@Param('id') id: string) {
        return { id };
    }

    @Post()
    create(@Body() body: any) {
        return body;
    }
}
"""
        obs = extract_javascript_file_observations("src/users.controller.ts", nest_code)
        self.assertEqual(obs[0].kind, "js.file")

if __name__ == "__main__":
    unittest.main()
