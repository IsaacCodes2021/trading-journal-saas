import type { Editor } from '@tiptap/react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import NotesEditor from './NotesEditor'

async function mount(html = '<p>hello world</p>') {
  const onChange = vi.fn()
  const onBlur = vi.fn()
  let editor: Editor | undefined
  const onReady = vi.fn((_start: string, e: Editor) => {
    editor = e
  })
  render(<NotesEditor html={html} onChange={onChange} onBlur={onBlur} onReady={onReady} />)
  await screen.findByRole('textbox', { name: 'Notes' })
  await waitFor(() => expect(editor).toBeDefined())
  const ed = editor as Editor
  const lastHtml = () => (onChange.mock.calls.at(-1) as [string] | undefined)?.[0]
  return { ed, onChange, onBlur, onReady, lastHtml, user: userEvent.setup() }
}

const toolbar = () => screen.getByRole('toolbar', { name: 'Formatting' })

describe('the notes editor', () => {
  it('reports the starting text as the editor writes it', async () => {
    const { onReady } = await mount('<p>one</p><p>two</p>')
    expect(onReady.mock.calls[0]?.[0]).toBe('<p>one</p><p>two</p>')
  })

  it('reports an empty note as an empty string, not as empty markup', async () => {
    const { onReady, ed, lastHtml } = await mount('')
    expect(onReady.mock.calls[0]?.[0]).toBe('')
    act(() => void ed.commands.setContent('<p>x</p>', { emitUpdate: true }))
    expect(lastHtml()).toBe('<p>x</p>')
    act(() => void ed.commands.clearContent(true))
    expect(lastHtml()).toBe('')
  })

  it.each([
    ['Bold', '<p><strong>hello world</strong></p>'],
    ['Italic', '<p><em>hello world</em></p>'],
    ['Underline', '<p><u>hello world</u></p>'],
    ['Bulleted list', '<ul><li><p>hello world</p></li></ul>'],
    ['Numbered list', '<ol><li><p>hello world</p></li></ol>'],
    ['Quote', '<blockquote><p>hello world</p></blockquote>'],
  ])('%s: applies to the selection, shows as pressed, and comes off again', async (name, expected) => {
    const { ed, lastHtml, user } = await mount()
    act(() => void ed.commands.setTextSelection({ from: 1, to: 12 })) // "hello world"
    const button = within(toolbar()).getByRole('button', { name })
    await user.click(button)
    expect(lastHtml()).toBe(expected)
    await waitFor(() => expect(button).toHaveAttribute('aria-pressed', 'true'))
    await user.click(button)
    expect(lastHtml()).toBe('<p>hello world</p>')
    await waitFor(() => expect(button).toHaveAttribute('aria-pressed', 'false'))
  })

  it('makes a link from an address typed in the box, adding https to a bare one', async () => {
    const { ed, lastHtml, user } = await mount()
    act(() => void ed.commands.selectAll())
    await user.click(within(toolbar()).getByRole('button', { name: 'Link' }))
    await user.type(screen.getByLabelText('Link address'), 'example.com/notes')
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    expect(lastHtml()).toContain('<a ')
    expect(lastHtml()).toContain('href="https://example.com/notes"')
    expect(screen.queryByLabelText('Link address')).not.toBeInTheDocument() // the box closed
  })

  it('refuses an address with a script scheme, and says why', async () => {
    const { ed, onChange, user } = await mount()
    act(() => void ed.commands.selectAll())
    await user.click(within(toolbar()).getByRole('button', { name: 'Link' }))
    await user.type(screen.getByLabelText('Link address'), 'javascript:alert(1)')
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Links must start with http://, https:// or mailto:.')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('removes a link', async () => {
    const { ed, lastHtml, user } = await mount('<p><a href="https://example.com">hello</a> world</p>')
    act(() => void ed.commands.setTextSelection(3))
    await user.click(within(toolbar()).getByRole('button', { name: 'Link' }))
    expect(screen.getByLabelText('Link address')).toHaveValue('https://example.com')
    await user.click(screen.getByRole('button', { name: 'Remove link' }))
    expect(lastHtml()).toBe('<p>hello world</p>')
  })

  it('cannot make anything the sanitizer would strip: headings, code, rules, scripts, images, other links', async () => {
    const { ed, lastHtml } = await mount()
    act(
      () =>
        void ed.commands.setContent(
          '<h1>title</h1><pre><code>code</code></pre><hr><script>alert(1)</script><img src=x onerror=alert(1)><p><a href="javascript:alert(1)">bad</a> <s>strike</s></p>',
          { emitUpdate: true },
        ),
    )
    const out = lastHtml() ?? ''
    for (const forbidden of ['<h1', '<pre', '<code', '<hr', '<script', '<img', 'javascript:', '<s>', 'onerror']) {
      expect(out).not.toContain(forbidden)
    }
    expect(out).toContain('title')
    expect(out).toContain('bad')
  })

  it('does not run markup in the note it is given', async () => {
    await mount('<p>safe</p><img src=x onerror=alert(1)><script>alert(1)</script>')
    const box = screen.getByRole('textbox', { name: 'Notes' })
    expect(box.querySelector('img')).toBeNull()
    expect(box.querySelector('script')).toBeNull()
    expect(box).toHaveTextContent('safe')
  })

  it('tells its owner when it loses focus', async () => {
    const { onBlur, user } = await mount()
    await user.click(screen.getByRole('textbox', { name: 'Notes' }))
    await user.click(document.body)
    await waitFor(() => expect(onBlur).toHaveBeenCalled())
  })

  it('inserts the template, with its three bold labels', async () => {
    const { lastHtml, user } = await mount('')
    await user.click(within(toolbar()).getByRole('button', { name: 'Insert template' }))
    expect(lastHtml()).toContain('<strong>Plan:</strong>')
    expect(lastHtml()).toContain('<strong>What happened:</strong>')
    expect(lastHtml()).toContain('<strong>Improve:</strong>')
  })
})
