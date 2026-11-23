import { describe, expect, it } from 'vitest'
import type { SecretStatus } from './types'
import {
  applyLibrarySecretStatuses,
  mergeSettledSecretStatuses,
} from './secret-status-utils'

function status(
  profileId: string,
  configured: boolean,
  scope: SecretStatus['scope'] = 'project',
): SecretStatus {
  return {
    profile_id: profileId,
    configured,
    origin: 'https://api.deepseek.com',
    scope,
  }
}

describe('mergeSettledSecretStatuses', () => {
  it('keeps known statuses when another profile request fails', () => {
    const current = {
      existing: status('existing', true),
      linked: status('linked', true, 'library'),
    }
    const refreshed = status('existing', false)
    const results: PromiseSettledResult<readonly [string, SecretStatus]>[] = [
      { status: 'fulfilled', value: ['existing', refreshed] },
      {
        status: 'rejected',
        reason: new Error('credential store unavailable'),
      },
    ]

    expect(
      mergeSettledSecretStatuses(current, ['existing', 'linked'], results),
    ).toEqual({
      existing: refreshed,
      linked: current.linked,
    })
  })

  it('removes statuses that do not belong to the active project', () => {
    const current = {
      current: status('current', true),
      previous: status('previous', true),
    }
    const results: PromiseSettledResult<readonly [string, SecretStatus]>[] = [
      {
        status: 'rejected',
        reason: new Error('profile is not persisted yet'),
      },
    ]

    expect(mergeSettledSecretStatuses(current, ['current'], results)).toEqual({
      current: current.current,
    })
  })
})

describe('applyLibrarySecretStatuses', () => {
  it('applies a library status that arrives after a profile is linked', () => {
    const profiles = [
      {
        id: 'deepseek-flash-2',
        library_id: 'deepseek-flash',
      },
    ]
    const libraryStatus = status('deepseek-flash', true, 'library')

    expect(
      applyLibrarySecretStatuses({}, profiles, {
        'deepseek-flash': libraryStatus,
      }),
    ).toEqual({
      'deepseek-flash-2': {
        ...libraryStatus,
        profile_id: 'deepseek-flash-2',
      },
    })
  })

  it('does not overwrite a project-specific credential', () => {
    const profiles = [
      {
        id: 'deepseek-flash-2',
        library_id: 'deepseek-flash',
      },
    ]
    const projectStatus = status('deepseek-flash-2', true, 'project')

    expect(
      applyLibrarySecretStatuses(
        { 'deepseek-flash-2': projectStatus },
        profiles,
        {
          'deepseek-flash': status('deepseek-flash', true, 'library'),
        },
      ),
    ).toEqual({
      'deepseek-flash-2': projectStatus,
    })
  })
})
