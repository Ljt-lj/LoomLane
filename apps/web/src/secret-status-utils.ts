import type { ApiProfile, SecretStatus } from './types'

type NamedSecretStatusResult = PromiseSettledResult<
  readonly [string, SecretStatus]
>

export function mergeSettledSecretStatuses(
  current: Record<string, SecretStatus>,
  activeProfileIds: Iterable<string>,
  results: readonly NamedSecretStatusResult[],
): Record<string, SecretStatus> {
  const activeIds = new Set(activeProfileIds)
  const next = Object.fromEntries(
    Object.entries(current).filter(([profileId]) => activeIds.has(profileId)),
  )

  for (const result of results) {
    if (result.status !== 'fulfilled') continue
    const [profileId, status] = result.value
    next[profileId] = status
  }

  return next
}

export function applyLibrarySecretStatuses(
  current: Record<string, SecretStatus>,
  profiles: Iterable<Pick<ApiProfile, 'id' | 'library_id'>>,
  libraryStatuses: Record<string, SecretStatus>,
): Record<string, SecretStatus> {
  const next = { ...current }

  for (const profile of profiles) {
    if (!profile.library_id) continue
    const libraryStatus = libraryStatuses[profile.library_id]
    if (!libraryStatus || next[profile.id]?.scope === 'project') continue
    next[profile.id] = {
      ...libraryStatus,
      profile_id: profile.id,
    }
  }

  return next
}
