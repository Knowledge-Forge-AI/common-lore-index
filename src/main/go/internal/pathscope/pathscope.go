// Package pathscope validates repository-relative Go source paths.
package pathscope

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
)

const MaxPathBytes = 4096

var (
	ErrInvalidPath      = errors.New("invalid repository-relative Go file path")
	ErrFileTooLarge     = errors.New("go file exceeds the configured size limit")
	ErrSourceUnreadable = errors.New("go source could not be read")
)

// Resolve returns the resolved regular Go file beneath root.
func Resolve(root string, relative string, maxBytes int64) (string, error) {
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", ErrInvalidPath
	}
	return ResolveFromCanonicalRoot(resolvedRoot, relative, maxBytes)
}

// ResolveFromCanonicalRoot retains the root chosen before an extraction session.
func ResolveFromCanonicalRoot(resolvedRoot string, relative string, maxBytes int64) (string, error) {
	if relative == "" || len([]byte(relative)) > MaxPathBytes {
		return "", ErrInvalidPath
	}
	if strings.ContainsRune(relative, '\x00') || filepath.IsAbs(relative) {
		return "", ErrInvalidPath
	}
	cleaned := filepath.Clean(filepath.FromSlash(relative))
	if cleaned == "." || cleaned == ".." || strings.HasPrefix(cleaned, ".."+string(filepath.Separator)) {
		return "", ErrInvalidPath
	}
	if filepath.Ext(cleaned) != ".go" {
		return "", ErrInvalidPath
	}

	resolvedCandidate, err := filepath.EvalSymlinks(filepath.Join(resolvedRoot, cleaned))
	if err != nil {
		return "", ErrInvalidPath
	}
	underRoot, err := filepath.Rel(resolvedRoot, resolvedCandidate)
	if err != nil || underRoot == ".." || strings.HasPrefix(underRoot, ".."+string(filepath.Separator)) {
		return "", ErrInvalidPath
	}
	info, err := os.Stat(resolvedCandidate)
	if err != nil || !info.Mode().IsRegular() {
		return "", ErrInvalidPath
	}
	if info.Size() > maxBytes {
		return "", ErrFileTooLarge
	}
	return resolvedCandidate, nil
}

// ReadSource snapshots at most maxBytes of a validated regular Go source.
func ReadSource(path string, maxBytes int64) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, ErrSourceUnreadable
	}
	defer func() { _ = file.Close() }()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, ErrSourceUnreadable
	}
	if info.Size() > maxBytes {
		return nil, ErrFileTooLarge
	}
	return readBoundedSource(file, maxBytes)
}

func readBoundedSource(reader io.Reader, maxBytes int64) ([]byte, error) {
	source, err := io.ReadAll(io.LimitReader(reader, maxBytes))
	if err != nil {
		return nil, ErrSourceUnreadable
	}
	if int64(len(source)) == maxBytes {
		var extra [1]byte
		count, err := io.ReadFull(reader, extra[:])
		if count != 0 {
			return nil, ErrFileTooLarge
		}
		if err != nil && !errors.Is(err, io.EOF) {
			return nil, ErrSourceUnreadable
		}
	}
	return source, nil
}
