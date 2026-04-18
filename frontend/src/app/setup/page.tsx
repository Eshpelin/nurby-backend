'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@/lib/auth';

export default function SetupPage() {
  const { register } = useAuth();
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await register(email, password, displayName);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className='flex min-h-[calc(100vh-3.5rem)] items-center justify-center px-4'>
      <div className='w-full max-w-sm space-y-6'>
        <div className='text-center space-y-1'>
          <h1 className='text-2xl font-semibold tracking-tight'>Create Admin Account</h1>
          <p className='text-sm text-muted-foreground'>
            Set up the first admin user for your Nurby instance.
          </p>
        </div>

        <form onSubmit={handleSubmit} className='space-y-4'>
          {error && (
            <div className='rounded-md bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400'>
              {error}
            </div>
          )}

          <div className='space-y-2'>
            <label htmlFor='display-name' className='text-sm font-medium text-foreground'>
              Display Name
            </label>
            <input
              id='display-name'
              type='text'
              required
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              className='w-full rounded-md border border-border bg-muted px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-accent'
              placeholder='Admin'
            />
          </div>

          <div className='space-y-2'>
            <label htmlFor='email' className='text-sm font-medium text-foreground'>
              Email
            </label>
            <input
              id='email'
              type='email'
              required
              autoComplete='email'
              value={email}
              onChange={e => setEmail(e.target.value)}
              className='w-full rounded-md border border-border bg-muted px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-accent'
              placeholder='admin@example.com'
            />
          </div>

          <div className='space-y-2'>
            <label htmlFor='password' className='text-sm font-medium text-foreground'>
              Password
            </label>
            <input
              id='password'
              type='password'
              required
              autoComplete='new-password'
              value={password}
              onChange={e => setPassword(e.target.value)}
              className='w-full rounded-md border border-border bg-muted px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-accent'
              placeholder='Choose a strong password'
            />
          </div>

          <button
            type='submit'
            disabled={submitting}
            className='w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-accent/90 disabled:opacity-50'
          >
            {submitting ? 'Creating account...' : 'Create account'}
          </button>
        </form>

        <p className='text-center text-sm text-muted-foreground'>
          Already set up?{' '}
          <Link href='/login' className='text-accent hover:underline'>
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
