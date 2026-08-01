import { remark } from 'remark'
import remarkFrontmatter from 'remark-frontmatter'
import remarkGfm from 'remark-gfm'
import remarkLint from 'remark-lint'
import remarkLintBlockquoteIndentation from 'remark-lint-blockquote-indentation'
import remarkLintCheckboxCharacterStyle from 'remark-lint-checkbox-character-style'
import remarkLintCodeBlockStyle from 'remark-lint-code-block-style'
import remarkLintEmphasisMarker from 'remark-lint-emphasis-marker'
import remarkLintFencedCodeMarker from 'remark-lint-fenced-code-marker'
import remarkLintHardBreakSpaces from 'remark-lint-hard-break-spaces'
import remarkLintHeadingStyle from 'remark-lint-heading-style'
import remarkLintLinkTitleStyle from 'remark-lint-link-title-style'
import remarkLintListItemBulletIndent from 'remark-lint-list-item-bullet-indent'
import remarkLintListItemIndent from 'remark-lint-list-item-indent'
import remarkLintNoBlockquoteWithoutMarker from 'remark-lint-no-blockquote-without-marker'
import remarkLintNoConsecutiveBlankLines from 'remark-lint-no-consecutive-blank-lines'
import remarkLintNoDuplicateDefinitions from 'remark-lint-no-duplicate-definitions'
import remarkLintNoHeadingContentIndent from 'remark-lint-no-heading-content-indent'
import remarkLintNoShortcutReferenceImage from 'remark-lint-no-shortcut-reference-image'
import remarkLintNoShortcutReferenceLink from 'remark-lint-no-shortcut-reference-link'
import remarkLintNoUnusedDefinitions from 'remark-lint-no-unused-definitions'
import remarkLintOrderedListMarkerStyle from 'remark-lint-ordered-list-marker-style'
import remarkLintRuleStyle from 'remark-lint-rule-style'
import remarkLintStrongMarker from 'remark-lint-strong-marker'
import remarkLintTableCellPadding from 'remark-lint-table-cell-padding'
import remarkMath from 'remark-math'

const PROFILE = Object.freeze({
  id: 'zettlr-remark',
  profile: 'zettlr-4.7.0'
})

const STYLE_RULE_IDS = new Set([
  'blockquote-indentation',
  'checkbox-character-style',
  'code-block-style',
  'emphasis-marker',
  'fenced-code-marker',
  'hard-break-spaces',
  'heading-style',
  'link-title-style',
  'list-item-bullet-indent',
  'list-item-indent',
  'no-blockquote-without-marker',
  'no-consecutive-blank-lines',
  'no-heading-content-indent',
  'no-shortcut-reference-image',
  'no-shortcut-reference-link',
  'no-unused-definitions',
  'ordered-list-marker-style',
  'rule-style',
  'strong-marker',
  'table-cell-padding'
])

const SEMANTIC_RULE_IDS = new Set([
  'no-duplicate-definitions'
])

function parseArguments (argv) {
  const options = {
    path: '<stdin>',
    italicFormatting: '_',
    boldFormatting: '**'
  }

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    const equalsIndex = argument.indexOf('=')
    const name = equalsIndex === -1 ? argument : argument.slice(0, equalsIndex)
    let value = equalsIndex === -1 ? undefined : argument.slice(equalsIndex + 1)

    if (!['--path', '--italic-formatting', '--bold-formatting'].includes(name)) {
      throw new Error(`Unknown argument: ${argument}`)
    }
    if (value === undefined) {
      index += 1
      value = argv[index]
    }
    if (value === undefined || value.length === 0) {
      throw new Error(`Missing value for ${name}`)
    }

    if (name === '--path') {
      options.path = value
    } else if (name === '--italic-formatting') {
      options.italicFormatting = value
    } else {
      options.boldFormatting = value
    }
  }

  if (!['*', '_', 'consistent'].includes(options.italicFormatting)) {
    throw new Error(
      'Invalid --italic-formatting value; expected *, _, or consistent'
    )
  }
  if (!['**', '__', 'consistent'].includes(options.boldFormatting)) {
    throw new Error(
      'Invalid --bold-formatting value; expected **, __, or consistent'
    )
  }
  return options
}

function effectiveConfiguration (options) {
  return {
    italic_formatting: options.italicFormatting,
    bold_formatting: options.boldFormatting,
    emphasis_marker: options.italicFormatting,
    strong_marker: options.boldFormatting === 'consistent'
      ? 'consistent'
      : options.boldFormatting === '**' ? '*' : '_'
  }
}

function nullableNumber (value) {
  return typeof value === 'number' ? value : null
}

function messagePosition (message) {
  const place = message.place
  if (place === undefined || place === null) {
    return {
      line: nullableNumber(message.line),
      column: nullableNumber(message.column),
      end_line: null,
      end_column: null
    }
  }
  if ('start' in place) {
    return {
      line: nullableNumber(place.start?.line),
      column: nullableNumber(place.start?.column),
      end_line: nullableNumber(place.end?.line),
      end_column: nullableNumber(place.end?.column)
    }
  }
  return {
    line: nullableNumber(place.line),
    column: nullableNumber(place.column),
    end_line: nullableNumber(place.line),
    end_column: nullableNumber(place.column)
  }
}

function classifyMessage (message) {
  const ruleId = typeof message.ruleId === 'string' ? message.ruleId : null
  if (message.fatal === true) {
    return { category: 'parser', blocking: true }
  }
  if (ruleId !== null && SEMANTIC_RULE_IDS.has(ruleId)) {
    return { category: 'semantic', blocking: true }
  }
  if (ruleId !== null && STYLE_RULE_IDS.has(ruleId)) {
    return { category: 'style', blocking: false }
  }
  return { category: 'semantic', blocking: true }
}

function normalizeDiagnostic (message) {
  const classification = classifyMessage(message)
  return {
    rule_id: typeof message.ruleId === 'string' ? message.ruleId : null,
    source: typeof message.source === 'string' ? message.source : null,
    severity: message.fatal === true ? 'error' : 'warning',
    category: classification.category,
    blocking: classification.blocking,
    message: String(message.reason ?? message.message ?? ''),
    ...messagePosition(message)
  }
}

function compareNullableNumbers (left, right) {
  const normalizedLeft = left === null ? Number.MAX_SAFE_INTEGER : left
  const normalizedRight = right === null ? Number.MAX_SAFE_INTEGER : right
  return normalizedLeft - normalizedRight
}

function compareStrings (left, right) {
  const normalizedLeft = left ?? ''
  const normalizedRight = right ?? ''
  if (normalizedLeft < normalizedRight) return -1
  if (normalizedLeft > normalizedRight) return 1
  return 0
}

function compareDiagnostics (left, right) {
  return compareNullableNumbers(left.line, right.line) ||
    compareNullableNumbers(left.column, right.column) ||
    compareStrings(left.rule_id, right.rule_id) ||
    compareStrings(left.message, right.message)
}

function countsFor (diagnostics) {
  return {
    diagnostics: diagnostics.length,
    errors: diagnostics.filter(item => item.severity === 'error').length,
    warnings: diagnostics.filter(item => item.severity === 'warning').length,
    blocking: diagnostics.filter(item => item.blocking).length,
    semantic: diagnostics.filter(item => item.category !== 'style').length,
    style: diagnostics.filter(item => item.category === 'style').length
  }
}

function resultFor (options, diagnostics) {
  const counts = countsFor(diagnostics)
  return {
    schema_version: 1,
    validator: PROFILE,
    file: options.path,
    config: effectiveConfiguration(options),
    status: counts.blocking > 0
      ? 'failed'
      : counts.diagnostics > 0 ? 'warnings' : 'pass',
    counts,
    diagnostics
  }
}

function failureResult (options, message) {
  return resultFor(options, [{
    rule_id: null,
    source: 'zettlr-validator',
    severity: 'error',
    category: 'parser',
    blocking: true,
    message,
    line: null,
    column: null,
    end_line: null,
    end_column: null
  }])
}

async function readUtf8Stdin () {
  const chunks = []
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  }
  return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks))
}

async function validate (value, options) {
  const config = effectiveConfiguration(options)
  const result = await remark()
    .use(remarkFrontmatter, [
      { type: 'yaml', fence: { open: '---', close: '...' } },
      { type: 'yaml', fence: { open: '---', close: '---' } }
    ])
    .use(remarkGfm)
    .use(remarkMath)
    .use(remarkLint)
    .use(remarkLintBlockquoteIndentation, 2)
    .use(remarkLintCheckboxCharacterStyle, {
      checked: 'consistent',
      unchecked: ' '
    })
    .use(remarkLintCodeBlockStyle, 'consistent')
    .use(remarkLintEmphasisMarker, config.emphasis_marker)
    .use(remarkLintFencedCodeMarker, 'consistent')
    .use(remarkLintHeadingStyle, 'consistent')
    .use(remarkLintLinkTitleStyle, '"')
    .use(remarkLintOrderedListMarkerStyle, '.')
    .use(remarkLintRuleStyle, 'consistent')
    .use(remarkLintStrongMarker, config.strong_marker)
    .use(remarkLintTableCellPadding, 'consistent')
    .use(remarkLintListItemBulletIndent)
    .use(remarkLintListItemIndent, 'one')
    .use(remarkLintNoBlockquoteWithoutMarker)
    .use(remarkLintHardBreakSpaces)
    .use(remarkLintNoDuplicateDefinitions)
    .use(remarkLintNoHeadingContentIndent)
    .use(remarkLintNoShortcutReferenceImage)
    .use(remarkLintNoShortcutReferenceLink)
    .use(remarkLintNoUnusedDefinitions)
    .use(remarkLintNoConsecutiveBlankLines)
    .process({ path: options.path, value })

  return result.messages
    .map(normalizeDiagnostic)
    .sort(compareDiagnostics)
}

let options = {
  path: '<stdin>',
  italicFormatting: '_',
  boldFormatting: '**'
}

try {
  options = parseArguments(process.argv.slice(2))
  const value = await readUtf8Stdin()
  const diagnostics = await validate(value, options)
  const result = resultFor(options, diagnostics)
  process.stdout.write(`${JSON.stringify(result)}\n`)
  process.exitCode = result.status === 'failed' ? 1 : 0
} catch (error) {
  const reason = error instanceof TypeError &&
      String(error.message).includes('encoded data was not valid')
    ? 'Input is not valid UTF-8'
    : `Validator execution failed: ${error instanceof Error ? error.message : String(error)}`
  process.stdout.write(`${JSON.stringify(failureResult(options, reason))}\n`)
  process.exitCode = 2
}
