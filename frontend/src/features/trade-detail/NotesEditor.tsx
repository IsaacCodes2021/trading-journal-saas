import { EditorContent, useEditor, useEditorState, type Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { useState, type FormEvent, type ReactNode } from 'react'
import { sanitize } from '../../components/SafeHtml'
import { Popover } from '../../components/ui/Popover'
import { Button } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import { linkUrl } from './linkUrl'

/** A starting point for a note, offered by "Insert template". */
export const NOTE_TEMPLATE =
  '<p><strong>Plan:</strong> </p><p><strong>What happened:</strong> </p><p><strong>Improve:</strong> </p>'

/*
 * Only what the sanitizer allows (doc 04: p, br, strong, em, u, lists, blockquote, links) can be made here. Code
 * blocks, headings, rules and strike-through are switched off, so a keyboard shortcut or a pasted block can never put
 * markup in a note that the server would then strip. Links are limited to http, https and mailto.
 */
const EXTENSIONS = [
  StarterKit.configure({
    code: false,
    codeBlock: false,
    heading: false,
    horizontalRule: false,
    strike: false,
    trailingNode: false, // it would add an empty paragraph after a list or quote, and save it
    link: { openOnClick: false, autolink: true, protocols: ['http', 'https', 'mailto'] },
  }),
]

function ToolButton({
  label,
  active,
  onClick,
  children,
  className,
}: {
  label: string
  active: boolean
  onClick: () => void
  children: ReactNode
  className?: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      // Keep the caret in the text: pressing a toolbar button must not take focus (and the selection) away from it.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className={cx(
        'h-[34px] min-w-[34px] cursor-pointer rounded-md border border-transparent bg-transparent px-2 font-[inherit] text-ink hover:bg-raised',
        active && 'bg-selected',
        className,
      )}
    >
      {children}
    </button>
  )
}

function LinkForm({ editor, close }: { editor: Editor; close: () => void }) {
  const [typed, setTyped] = useState(() => (editor.getAttributes('link')['href'] as string | undefined) ?? '')
  const [problem, setProblem] = useState<string | null>(null)
  const hasLink = editor.isActive('link')

  const apply = (event: FormEvent) => {
    event.preventDefault()
    const result = linkUrl(typed)
    if (result.problem !== null) {
      setProblem(result.problem)
      return
    }
    editor.chain().focus().extendMarkRange('link').setLink({ href: result.url }).run()
    close()
  }

  return (
    <form onSubmit={apply} className="flex min-w-[260px] flex-col gap-2.5 p-1">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Link address
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="https://"
          className="h-9 rounded-lg border border-line-strong bg-bg px-2.5 font-[inherit] text-[14px] text-ink"
        />
      </label>
      {problem ? (
        <p role="alert" className="m-0 text-xs text-neg">
          {problem}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="submit" variant="primary">
          Apply
        </Button>
        {hasLink ? (
          <Button
            onClick={() => {
              editor.chain().focus().extendMarkRange('link').unsetLink().run()
              close()
            }}
          >
            Remove link
          </Button>
        ) : null}
      </div>
    </form>
  )
}

function Toolbar({ editor }: { editor: Editor }) {
  // The toolbar re-renders when the selection or the marks under it change, not on every keystroke.
  const on = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive('bold'),
      italic: e.isActive('italic'),
      underline: e.isActive('underline'),
      bullet: e.isActive('bulletList'),
      ordered: e.isActive('orderedList'),
      quote: e.isActive('blockquote'),
      link: e.isActive('link'),
    }),
  })
  return (
    <div
      role="toolbar"
      aria-label="Formatting"
      className="flex flex-wrap items-center gap-1 border-b border-line px-3.5 py-2"
    >
      <ToolButton
        label="Bold"
        active={on.bold}
        onClick={() => editor.chain().focus().toggleBold().run()}
        className="font-bold"
      >
        B
      </ToolButton>
      <ToolButton
        label="Italic"
        active={on.italic}
        onClick={() => editor.chain().focus().toggleItalic().run()}
        className="italic"
      >
        I
      </ToolButton>
      <ToolButton
        label="Underline"
        active={on.underline}
        onClick={() => editor.chain().focus().toggleUnderline().run()}
        className="underline"
      >
        U
      </ToolButton>
      <ToolButton
        label="Bulleted list"
        active={on.bullet}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
      >
        • List
      </ToolButton>
      <ToolButton
        label="Numbered list"
        active={on.ordered}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
      >
        1. List
      </ToolButton>
      <ToolButton label="Quote" active={on.quote} onClick={() => editor.chain().focus().toggleBlockquote().run()}>
        &ldquo; &rdquo;
      </ToolButton>
      <Popover
        label="Link"
        button="Link"
        buttonClassName={cx(
          'h-[34px] border-transparent bg-transparent px-2 hover:bg-raised',
          on.link && 'bg-selected',
        )}
      >
        {(close) => <LinkForm editor={editor} close={close} />}
      </Popover>
      <span className="grow" />
      <Button
        className="h-[30px]"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => editor.chain().focus().insertContent(NOTE_TEMPLATE).run()}
      >
        Insert template
      </Button>
    </div>
  )
}

/**
 * The rich-text editor for a trade's note (Tiptap). Loaded on demand, because it is the biggest piece of the page.
 * It reports the note as HTML (empty string for an empty note) and tells the caller when it loses focus; saving is the
 * caller's job.
 */
export default function NotesEditor({
  html,
  onChange,
  onBlur,
  onReady,
}: {
  html: string
  onChange: (html: string) => void
  onBlur: () => void
  /** Hands over the editor once it exists: the starting text as the editor will write it, and the editor itself. */
  onReady?: (startHtml: string, editor: Editor) => void
}) {
  const editor = useEditor({
    extensions: EXTENSIONS,
    content: sanitize(html),
    // Tiptap would inject a <style> element; the content security policy blocks that, so styles.css has its rules.
    injectCSS: false,
    editorProps: {
      attributes: { role: 'textbox', 'aria-multiline': 'true', 'aria-label': 'Notes' },
    },
    onCreate: ({ editor: e }) => onReady?.(e.isEmpty ? '' : e.getHTML(), e),
    onUpdate: ({ editor: e }) => onChange(e.isEmpty ? '' : e.getHTML()),
    onBlur: () => onBlur(),
  })

  return (
    <>
      {editor ? <Toolbar editor={editor} /> : null}
      <div className="notes-editor px-5 py-4 text-[15px] leading-relaxed">
        <EditorContent editor={editor} />
      </div>
    </>
  )
}
