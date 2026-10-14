import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { components } from './api/schema'

export type AdminTemplateKey = components['schemas']['RuleTemplateKeyOut']
export type AdminTemplate = components['schemas']['AdminRuleTemplateOut']
export type TemplateImport = components['schemas']['RuleTemplateImportOut']
export type TemplateEdit = components['schemas']['RuleTemplateEditIn']

export const TEMPLATES_PAGE = 50

export interface TemplateQuery {
  /** Words of a firm, program, size, phase or version. */
  q: string
  /** 1-based. */
  page: number
}

const KEY = ['admin-rules'] as const

/** The rule fields and their groups, in the form's order (the same list the people's rules form uses). */
export function useTemplateFields() {
  return useQuery({
    queryKey: [...KEY, 'fields'],
    queryFn: () => unwrap(api.GET('/api/v1/rule-fields')),
    staleTime: Infinity,
    retry: false,
  })
}

/** One page of the firms' templates. The last page stays on screen while the next one loads. */
export function useAdminTemplates(query: TemplateQuery) {
  return useQuery({
    queryKey: [...KEY, 'list', query],
    queryFn: () =>
      unwrap(
        api.GET('/api/admin/v1/rule-templates', {
          params: {
            query: {
              ...(query.q ? { q: query.q } : {}),
              limit: TEMPLATES_PAGE,
              offset: (query.page - 1) * TEMPLATES_PAGE,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/** One template in full: every value, its flags and the source's own words for the cells it could not read. */
export function useAdminTemplate(id: number | null) {
  return useQuery({
    queryKey: [...KEY, 'one', id],
    queryFn: () =>
      unwrap(api.GET('/api/admin/v1/rule-templates/{template_id}', { params: { path: { template_id: id ?? 0 } } })),
    enabled: id !== null,
    refetchOnWindowFocus: false, // an open form is not replaced under the operator's hands
    retry: false,
  })
}

/** Replace one template's values and flags. People's verified rule sets are copies and do not change. */
export function useEditTemplate(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (edit: TemplateEdit) =>
      unwrap(
        api.PUT('/api/admin/v1/rule-templates/{template_id}', { params: { path: { template_id: id } }, body: edit }),
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData([...KEY, 'one', id], saved)
      return queryClient.invalidateQueries({ queryKey: [...KEY, 'list'] })
    },
  })
}

/** The upload is multipart, not JSON: the browser writes the boundary itself, so no content type is set here. */
function csvForm(file: File): FormData {
  const form = new FormData()
  form.append('file', file)
  return form
}

/** Load the research CSV, or with `dryRun` work out what loading it would do and keep nothing. */
export function useImportTemplates() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, dryRun }: { file: File; dryRun: boolean }) =>
      unwrap(
        api.POST('/api/admin/v1/rule-templates/import', {
          params: { query: dryRun ? { dry_run: true } : {} },
          body: { file: file as unknown as string },
          bodySerializer: () => csvForm(file),
        }),
      ),
    onSuccess: (result) =>
      result.applied ? queryClient.invalidateQueries({ queryKey: KEY, refetchType: 'active' }) : undefined,
  })
}

/** Who changed a template last, in words. */
export function changedBy(updatedBy: string, self: string): string {
  if (updatedBy === 'seed') return 'the first import'
  if (updatedBy === 'cli:owner') return 'the owner’s command line'
  if (updatedBy === `admin:${self}`) return 'you'
  if (updatedBy.startsWith('admin:')) return 'another operator'
  return updatedBy
}
