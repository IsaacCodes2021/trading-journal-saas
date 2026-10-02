import { useQueryClient } from '@tanstack/react-query'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api/client'

export function NotesEditor({ tradeId, initial }: { tradeId: number; initial: string }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('Saved')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const editor = useEditor({
    extensions: [StarterKit],
    content: initial,
    // By default Tiptap injects a <style> element at runtime, which a strict CSP blocks. The few base rules it
    // needs live in styles.css instead.
    injectCSS: false,
    onUpdate: ({ editor: e }) => {
      setStatus('Editing…')
      clearTimeout(timer.current)
      timer.current = setTimeout(async () => {
        await api.PUT('/api/v1/trades/{trade_id}/notes', {
          params: { path: { trade_id: tradeId } },
          body: { notes: e.getHTML() },
        })
        setStatus('Saved')
        await queryClient.invalidateQueries({ queryKey: ['trade', tradeId] })
      }, 600)
    },
  })
  useEffect(() => () => clearTimeout(timer.current), [])
  return (
    <div>
      <p className="text-sm" data-testid="notes-status">
        {status}
      </p>
      <EditorContent editor={editor} className="notes-editor rounded border p-2" />
    </div>
  )
}

export default NotesEditor
