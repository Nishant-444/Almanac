# useMemo

`useMemo` is a React hook that lets you cache the result of a calculation between re-renders.

## Reference

```js
const cachedValue = useMemo(calculateValue, dependencies)
```

Call `useMemo` at the top level of your component to cache a calculation between re-renders. React calls your calculation function during the initial render and returns its result. On later renders it returns the cached value unless one of the dependencies has changed.

## Caveats

Do not rely on `useMemo` for correctness: React may discard cached values. Use it only as a performance optimization alongside `memo` for components.
