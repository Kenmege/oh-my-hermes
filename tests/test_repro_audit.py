"""Real OMH surface tests; native result bytes in this module are synthetic."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = sys.platform == 'darwin' or sys.platform.startswith('linux')

class AuditSurface(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'omh.cli', 'paper', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True)

    @unittest.skipUnless(SUPPORTED, 'audit filesystem requires macOS/Linux')
    def test_init_audit_unknown_and_new_only_outputs(self):
        case, out = self.root / 'case', self.root / 'report'
        result = self.cli('audit-init', '--out', case)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('omh', result.stdout)
        before = {p.name: p.read_bytes() for p in case.glob('*.json')}
        value = json.loads((case / 'case.json').read_text())
        value['case_id'] = '<script>alert(1)</script>'
        (case / 'case.json').write_text(json.dumps(value))
        result = self.cli('audit', '--case', case, '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((out / 'report.json').read_text())
        self.assertEqual(report['comparison']['overall'], 'blocked_unknown')
        self.assertEqual(report['scientific_verdict'], 'not_assessed')
        html = (out / 'report.html').read_text()
        self.assertIn('lang="en"', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('src="http', html)
        self.assertNotIn('href=', html)
        saved = {p.name: p.read_bytes() for p in out.iterdir()}
        result = self.cli('audit', '--case', case, '--out', out)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(saved, {p.name: p.read_bytes() for p in out.iterdir()})
        self.assertEqual(before['reported.json'], (case / 'reported.json').read_bytes())
        self.assertEqual(self.cli('audit', '--case', case, '--out', case / 'nested').returncode, 2)


@unittest.skipUnless(SUPPORTED, 'audit filesystem requires macOS/Linux')
class NativeImport(AuditSurface):
    def test_negative_zero_source_token_is_preserved(self):
        source = self.root / 'source.json'
        raw = b'{"results":{"hellaswag":{"acc":-0}}}'
        source.write_bytes(raw)
        out = self.root / 'out'
        p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
        self.assertEqual(p.returncode, 0, p.stderr)
        review = json.loads((out / 'import-review.json').read_text())
        self.assertEqual(review['source_number_token'], '-0')
        actual = json.loads((out / 'actual.json').read_text())
        self.assertEqual(actual['score']['raw_value'], '-0')
        self.assertEqual(source.read_bytes(), raw)

    def test_source_token_boundaries_and_integer_fewshot_claims(self):
        for token, value in (('0', '0'), ('1', '1'), ('0.1200', '0.1200'),
                             ('1.20e-1', '0.120')):
            for fewshot in ('0', '5', '-0'):
                with self.subTest(token=token, fewshot=fewshot):
                    source = self.root / 'source.json'
                    raw = ('{"results":{"hellaswag":{"acc":' + token + '}},'
                           '"config":{"num_fewshot":' + fewshot + '}}').encode()
                    source.write_bytes(raw)
                    out = self.root / (token + '-' + fewshot)
                    p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    review = json.loads((out / 'import-review.json').read_text())
                    self.assertEqual(review['source_number_token'], token)
                    actual = json.loads((out / 'actual.json').read_text())
                    self.assertEqual(actual['score']['raw_value'], value)
                    count = actual['values']['fewshot.count']['value']
                    self.assertIs(type(count), int)
                    self.assertEqual(count, int(fewshot))
                    extract = json.loads((out / 'artifacts/harness-extract.json').read_text())
                    self.assertIs(type(extract['num_fewshot']), int)
                    self.assertEqual(extract['num_fewshot'], int(fewshot))
                    self.assertEqual(source.read_bytes(), raw)

    def test_noninteger_fewshot_tokens_are_refused(self):
        for field in ('"config":{"num_fewshot":%s}',
                      '"configs":{"hellaswag":{"num_fewshot":%s}}'):
            for i, token in enumerate(('true', 'false', '"0"', '"5"', '0.0', '5.0', '0e0', '5e0')):
                with self.subTest(field=field, token=token):
                    source = self.root / 'source.json'
                    raw = ('{"results":{"hellaswag":{"acc":-0}},' + field % token + '}').encode()
                    source.write_bytes(raw)
                    out = self.root / ('bad-' + str(i) + ('-task' if 'configs' in field else '-global'))
                    p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
                    self.assertEqual(p.returncode, 2, p.stderr)
                    self.assertIn('invalid_fewshot', (out / 'validation.json').read_text())
                    self.assertEqual([x.name for x in out.iterdir()], ['validation.json'])
                    self.assertNotIn('Traceback', p.stderr)
                    self.assertEqual(source.read_bytes(), raw)

    def test_unselected_structural_limits_and_ambiguous_filters_reject(self):
        base = '{"results":{"hellaswag":{"acc":0.2}},"ignored":%s}'
        raws = [base % ('[' * 40 + '0' + ']' * 40), base % ('1' * 1300),
                '{"results":{"hellaswag":{"acc":0.2,"acc,other":0.3}}}']
        for i, raw in enumerate(raws):
            source = self.root / 'source.json'
            source.write_text(raw)
            p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', self.root / str(i))
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertNotIn('Traceback', p.stderr)

    def test_exponent_and_task_local_fewshot_preserve_exact_value(self):
        source = self.root / 'new.json'
        source.write_text('{"results":{"hellaswag":{"acc_norm,none":1.2300000000000000000001e-1}},"configs":{"hellaswag":{"num_fewshot":5}}}')
        out = self.root / 'out'
        p = self.cli('import-harness', '--results', source, '--metric', 'acc_norm', '--out', out)
        self.assertEqual(p.returncode, 0, p.stderr)
        from omh.workflows.repro_audit.core import load_case
        b = load_case(out)
        self.assertEqual(b['actual']['score']['raw_value'], '0.12300000000000000000001')
        self.assertEqual(b['actual']['values']['fewshot.count']['value'], 5)
        self.assertEqual(b['case']['profile_id'], 'hellaswag-acc-norm-audit-v0.2')
        review = json.loads((out / 'import-review.json').read_text())
        self.assertEqual(review['source_number_token'], '1.2300000000000000000001e-1')
        self.assertIn('/configs/hellaswag/num_fewshot', review['original_json_pointers'])

    def test_invalid_native_inputs_reject_without_echo_or_partial_success(self):
        cases = ['{', '{"results":{"hellaswag":{"acc":0.2,"acc":0.3}}}',
                 '{"results":{"hellaswag":{"acc":NaN}}}',
                 '{"results":{"hellaswag":{"acc":Infinity}}}',
                 '{"results":{"hellaswag":{"acc":0.2,"acc,none":0.2}}}',
                 '{"results":{"hellaswag":{"acc":0.2,"acc,none":0.3}}}',
                 '{"results":{"other":{"acc":0.2}}}',
                 '{"results":{"hellaswag":{"acc_norm":0.2}}}']
        for value in ('true', 'null', '"0.2"', '-0.1', '1.1', '1e99999999', '1e-99999999'):
            cases.append('{"results":{"hellaswag":{"acc":' + value + '}}}')
        for extra in ('"config":{"num_fewshot":true}',
                      '"config":{"num_fewshot":0},"configs":{"hellaswag":{"num_fewshot":5}}'):
            cases.append('{"results":{"hellaswag":{"acc":0.2}},' + extra + '}')
        for i, raw in enumerate(cases):
            with self.subTest(index=i):
                source = self.root / 'bad.json'
                source.write_text(raw)
                out = self.root / ('bad-' + str(i))
                p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertNotIn('Traceback', p.stderr)
                self.assertNotIn(str(source), p.stderr)
                self.assertEqual([x.name for x in out.iterdir()], ['validation.json'])
                self.assertEqual(source.read_text(), raw)

    def test_paths_required_metric_and_overwrite_are_safe(self):
        source = self.root / 'source.json'
        source.write_text('{"results":{"hellaswag":{"acc":0.2}}}')
        out = self.root / 'out'
        p = self.cli('import-harness', '--results', source, '--out', out)
        self.assertEqual(p.returncode, 2)
        self.assertFalse(out.exists())
        link = self.root / 'link.json'
        link.symlink_to(source)
        for unsafe in (link, self.root / '..' / self.root.name / 'source.json', self.root):
            p = self.cli('import-harness', '--results', unsafe, '--metric', 'acc', '--out', out)
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertFalse(out.exists())
        p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
        self.assertEqual(p.returncode, 0, p.stderr)
        before = {str(x.relative_to(out)): x.read_bytes() for x in out.rglob('*') if x.is_file()}
        p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(before, {str(x.relative_to(out)): x.read_bytes() for x in out.rglob('*') if x.is_file()})

    def test_null_fewshot_is_absent_not_invalid(self):
        source = self.root / 'source.json'
        source.write_text('{"results":{"hellaswag":{"acc":0.2}},"config":{"num_fewshot":null},'
                          '"configs":{"hellaswag":{"num_fewshot":5}}}')
        out = self.root / 'out'
        p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
        self.assertEqual(p.returncode, 0, p.stderr)
        actual = json.loads((out / 'actual.json').read_text())
        self.assertEqual(actual['values']['fewshot.count']['value'], 5)
        review = json.loads((out / 'import-review.json').read_text())
        self.assertNotIn('/config/num_fewshot', review['original_json_pointers'])

    def test_unresolved_temp_and_tmp_paths_are_accepted(self):
        # The default macOS temp dir sits under the /var alias and /tmp is one
        # itself; neither may be refused as a symlink (#2242 review).
        unresolved = Path(self.temp.name)
        source = unresolved / 'source.json'
        source.write_text('{"results":{"hellaswag":{"acc":0.2}}}')
        p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', unresolved / 'imported')
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.cli('audit', '--case', unresolved / 'imported', '--out', unresolved / 'report')
        self.assertEqual(p.returncode, 0, p.stderr)
        if Path('/tmp').is_dir():
            with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
                p = self.cli('audit-init', '--out', Path(tmp) / 'case')
                self.assertEqual(p.returncode, 0, p.stderr)

    @unittest.skipUnless(Path('/tmp').is_symlink(), 'needs the macOS /tmp alias')
    def test_report_inside_case_is_refused_through_the_tmp_alias(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            case = Path(tmp) / 'case'
            self.assertEqual(self.cli('audit-init', '--out', case).returncode, 0)
            # The same directory spelled through /private, the alias's target.
            inside = Path('/private') / case.relative_to('/') / 'report'
            p = self.cli('audit', '--case', case, '--out', inside)
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertFalse(inside.exists())

    def test_old_and_new_exact_source_claims_audit_and_minimal_export(self):
        for key in ('acc', 'acc,none'):
            source = self.root / ('source-' + key + '.json')
            raw = ('{"results":{"hellaswag":{"' + key + '":0.1234567890123456789012345678900,"acc_norm":0.8}},'
                   '"config":{"num_fewshot":0,"model_args":"password=NEVER_EXPORT;exec(code)"},'
                   '"authorization":"NEVER_EXPORT"}').encode()
            source.write_bytes(raw)
            out = self.root / ('case-' + key)
            p = self.cli('import-harness', '--results', source, '--metric', 'acc', '--out', out)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('pending', p.stdout)
            self.assertEqual(source.read_bytes(), raw)
            from omh.workflows.repro_audit.core import load_case
            b = load_case(out)
            self.assertEqual(b['actual']['score']['raw_value'], '0.1234567890123456789012345678900')
            self.assertEqual(b['actual']['values']['metric.definition']['value']['name'], 'acc')
            self.assertEqual(b['actual']['values']['fewshot.count']['value'], 0)
            self.assertEqual(b['actual']['values']['scoring.spec_digest']['state'], 'unknown')
            self.assertTrue(all(v['state'] == 'unknown' for v in b['reported']['values'].values()))
            self.assertTrue(all(e['confirmed'] is False and e['level'] == 'manual_declaration' for e in b['evidence']['evidence']))
            review = json.loads((out / 'import-review.json').read_text())
            self.assertEqual(review['operator_review'], 'pending')
            self.assertEqual(review['original_input_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertIn('/results/hellaswag/' + key, review['original_json_pointers'])
            extract = out / review['derived_extract_path']
            self.assertEqual(review['derived_extract_sha256'], hashlib.sha256(extract.read_bytes()).hexdigest())
            for path in out.rglob('*'):
                if path.is_file():
                    self.assertNotIn('NEVER_EXPORT', path.read_text())
                    self.assertNotIn('model_args', path.read_text())
            audit = self.root / ('audit-' + key)
            p = self.cli('audit', '--case', out, '--out', audit)
            self.assertEqual(p.returncode, 0, p.stderr)
            report = json.loads((audit / 'report.json').read_text())
            self.assertEqual(report['comparison']['overall'], 'blocked_unknown')
            self.assertEqual(report['imported_run']['effective_state'], 'not_run')
            self.assertEqual(report['metric']['state'], 'not_computed')
            self.assertEqual(report['scientific_verdict'], 'not_assessed')
            self.assertEqual(len(list(audit.iterdir())), 5)


class ChatDiscovery(unittest.TestCase):
    def test_research_audit_chat_and_non_audit_controls(self):
        from omh.wrapper.contract import build_chat_interaction_payload
        from omh.skills.catalog import installable_skill_definitions
        self.assertIn('repro-audit', {s.name for s in installable_skill_definitions()})
        payload = build_chat_interaction_payload("Compare this HellaSwag log to the paper's reported conditions", source='discord')
        self.assertEqual(payload['route']['selected_skill'], 'repro-audit')
        self.assertEqual(payload['chat_response']['kind'], 'repro_audit')
        for message in ('Explain this paper in simple terms', 'Review this pull request for bugs',
                        'Explain the word audit in this essay', 'List the reported conditions for this patient',
                        'Compare evaluation results for our sales team', 'Generate a HellaSwag log for my checkpoint'):
            other = build_chat_interaction_payload(message, source='discord')
            self.assertNotEqual(other['route'].get('selected_skill'), 'repro-audit')

class ExecutionBackstop(unittest.TestCase):
    def test_execution_words_refuse_and_domain_nouns_do_not(self):
        # The second guard behind the allow-listed shape (#2324 review): it
        # must hold even if a later change widens a slot.
        from omh.routing.recommend import _REPRO_AUDIT_EXECUTION
        for word in ('benchmark', 're-benchmark', 'rebenchmark', 'run', 're-run', 'rerunning', 'retrain',
                     'fine-tune', 'finetuning', 're-evaluating', 'again', 'redo', 'produce'):
            with self.subTest(word=word):
                self.assertTrue(_REPRO_AUDIT_EXECUTION.fullmatch(word))
        for word in ('evaluation', 'results', 'score', 'paper', 'reported', 'metric', 'reproduce'):
            with self.subTest(word=word):
                self.assertIsNone(_REPRO_AUDIT_EXECUTION.fullmatch(word))

    def test_a_widened_slot_still_refuses_an_execution_word(self):
        from omh.routing import recommend
        message = recommend.normalized_phrase("Compare this HellaSwag run with the paper's reported metric")
        widened = recommend._REPRO_AUDIT_RECORD_WORDS | {'run'}
        with patch.object(recommend, '_REPRO_AUDIT_RECORD_WORDS', widened):
            self.assertFalse(recommend._repro_audit_offers_itself(message, set()))
            self.assertTrue(recommend._repro_audit_offers_itself(
                message.replace(' run ', ' log '), set()))


class PlatformBoundary(unittest.TestCase):
    def test_unsupported_platform_is_controlled_and_writes_nothing(self):
        from argparse import Namespace
        from omh.commands.paper import cmd_paper_audit
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / 'out'
            with patch('omh.workflows.repro_audit.operations.sys.platform', 'win32'):
                self.assertEqual(cmd_paper_audit(Namespace(audit_command='init', out=str(out), metric='acc')), 2)
            self.assertFalse(out.exists())

class SystemAliasBoundary(unittest.TestCase):
    def test_only_top_level_macos_aliases_are_trusted(self):
        from _toolkit_path_guard import ALIAS_CASES
        from omh.workflows.repro_audit.core import system_alias
        for platform, part, link_text, trusted in (*ALIAS_CASES, ('darwin', '/x/var', 'private/var', False)):
            with self.subTest(platform=platform, part=part, link=link_text), \
                    patch('sys.platform', platform), patch('os.readlink', return_value=link_text):
                self.assertIs(system_alias(Path(part)), trusted)

    # `_publish` is the POSIX descriptor path; `run` refuses other platforms
    # before reaching it (PlatformBoundary), so the call itself is POSIX-only.
    @unittest.skipUnless(SUPPORTED, 'audit filesystem requires macOS/Linux')
    def test_rollback_failure_keeps_the_original_error(self):
        from omh.workflows.repro_audit import operations
        with tempfile.TemporaryDirectory() as root:
            out = Path(root).resolve() / 'out'
            with patch.object(operations, '_rename_noreplace', side_effect=OSError(5, 'publication failed')), \
                    patch.object(operations.os, 'rmdir', side_effect=OSError(66, 'not empty')):
                with self.assertRaises(OSError) as raised:
                    operations._publish(str(out), {'a.json': b'{}'}, ('artifacts',))
            self.assertEqual(raised.exception.strerror, 'publication failed')

    def test_audit_subcommand_keeps_the_top_level_command(self):
        from omh.commands.main import build_parser
        args = build_parser().parse_args(['paper', 'audit-init', '--out', 'case'])
        self.assertEqual((args.command, args.audit_command), ('paper', 'init'))


class DistributionContract(unittest.TestCase):
    def test_explicit_package_and_contract_resource_registration(self):
        import tomllib
        config = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['setuptools']
        self.assertIn('omh.workflows.repro_audit', config['packages'])
        self.assertIn('data/contract.json', config['package-data']['omh.workflows.repro_audit'])

from omh.workflows.repro_audit.core import load_case, compare_case, ValidationError
from repro_audit_case import SyntheticCase
NORM = "hellaswag-acc-norm-audit-v0.2"

@unittest.skipUnless(SUPPORTED, 'audit filesystem requires macOS/Linux')
class Profiles(unittest.TestCase):
    def test_profile_allowlist_rejects_non_string_and_unknown(self):
        f = SyntheticCase()
        self.addCleanup(f.close)
        for value in ([], {}, None, 'hellaswag-acc-norm-audit-v9'):
            with self.subTest(value=value):
                f.records['case']['profile_id'] = value
                with self.assertRaises(ValidationError):
                    load_case(f.flush())

    def test_norm_profile_exact_display_and_cross_metric(self):
        f = SyntheticCase()
        self.addCleanup(f.close)
        f.records['case']['profile_id'] = NORM
        for role in ('reported', 'actual'):
            f.env(role, 'metric.definition')['value']['name'] = 'acc_norm'
        f.completed('73.90', '0.7389962158932484')
        f.records['reported']['score'].update(raw_unit='percent', source_rounding='rounded')
        report = compare_case(load_case(f.flush()))
        self.assertEqual(report['profile_id'], NORM)
        self.assertEqual(report['metric']['state'], 'outside_tolerance')
        self.assertEqual(report['metric']['tolerance_fraction'], '0')
        self.assertIn('source_rounding_warning', report['comparison']['warnings'])
        f.env('actual', 'metric.definition')['value']['name'] = 'acc'
        self.assertEqual(compare_case(load_case(f.flush()))['comparison']['overall'], 'blocked_mismatch')
        f.env('reported', 'metric.definition')['value']['name'] = 'acc'
        with self.assertRaises(ValidationError):
            load_case(f.flush())
